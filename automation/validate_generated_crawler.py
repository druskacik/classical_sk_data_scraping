from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
from contextlib import ExitStack
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse


METADATA_PATTERN = re.compile(
    r"<!-- crawler-factory-metadata\s*(\{.*?\})\s*-->",
    re.DOTALL,
)
REQUIRED_RECORD_FIELDS = {"title", "date", "url", "source", "source_url", "country_code"}
ALLOWED_REASON_CODES = {
    "no_current_events",
    "access_blocked",
    "no_parseable_source",
    "implementation_failed",
}


class ValidationError(RuntimeError):
    pass


def parse_blocked_metadata(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = METADATA_PATTERN.search(text)
    if not match:
        raise ValidationError("BLOCKED.md has no crawler-factory metadata block")
    try:
        metadata = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"BLOCKED.md metadata is invalid JSON: {exc}") from exc

    required = {"url", "country_code", "reason_code", "attempted_at", "retry_after"}
    missing = sorted(required - metadata.keys())
    if missing:
        raise ValidationError(f"BLOCKED.md metadata is missing: {', '.join(missing)}")
    if metadata["reason_code"] not in ALLOWED_REASON_CODES:
        raise ValidationError(f"Unsupported reason_code: {metadata['reason_code']!r}")
    attempted = date.fromisoformat(metadata["attempted_at"])
    retry_after = date.fromisoformat(metadata["retry_after"])
    if (retry_after - attempted).days != 30:
        raise ValidationError("retry_after must be exactly 30 days after attempted_at")
    return metadata


def validate_blocked(
    path: Path,
    url: str | None,
    country_code: str,
    attempted_on: date | None = None,
) -> dict:
    metadata = parse_blocked_metadata(path)
    if url is not None and metadata["url"] != url:
        raise ValidationError("BLOCKED.md URL does not match the requested source")
    if metadata["country_code"].upper() != country_code.upper():
        raise ValidationError("BLOCKED.md country code does not match the requested country")
    if attempted_on is not None and date.fromisoformat(metadata["attempted_at"]) != attempted_on:
        raise ValidationError("BLOCKED.md attempted_at does not match this run")
    text = path.read_text(encoding="utf-8")
    if len(text.split()) < 40:
        raise ValidationError("BLOCKED.md does not contain enough investigation evidence")
    return {"status": "blocked", "metadata": metadata}


def _valid_http_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return urlparse(value).scheme in {"http", "https"} and bool(urlparse(value).netloc)


def _validate_records(records: list[dict], crawler, expected_url: str, country_code: str) -> list[dict]:
    frame = crawler.transform(crawler.build_dataframe(records))
    for column, value in crawler.config.front_fields:
        if column not in frame.columns:
            frame.insert(0, column, value)
    if "country_code" not in frame.columns:
        frame.insert(0, "country_code", crawler.config.country_code)
    normalized = frame.to_dict(orient="records")

    errors = []
    seen = set()
    duplicate_count = 0
    for index, record in enumerate(normalized):
        missing = [field for field in REQUIRED_RECORD_FIELDS if not record.get(field)]
        if missing:
            errors.append(f"record {index} missing {', '.join(sorted(missing))}")
            continue
        try:
            date.fromisoformat(str(record["date"]))
        except ValueError:
            errors.append(f"record {index} has invalid date {record['date']!r}")
        for field in ("url", "source_url"):
            if not _valid_http_url(record[field]):
                errors.append(f"record {index} has invalid {field}")
        if str(record["country_code"]).upper() != country_code.upper():
            errors.append(f"record {index} has wrong country_code")
        key = (record.get("title"), record.get("date"), record.get("time_from"), record.get("url"))
        duplicate_count += key in seen
        seen.add(key)

    if normalized and duplicate_count / len(normalized) > 0.2:
        errors.append("more than 20% of records are duplicates")
    if errors:
        raise ValidationError("; ".join(errors[:10]))
    return normalized


def validate_crawler(
    workspace: Path,
    crawler_dir: Path,
    url: str | None,
    country_code: str,
    attempted_on: date | None = None,
) -> dict:
    relative_dir = crawler_dir.relative_to(workspace)
    module_name = ".".join((*relative_dir.parts, "main"))
    sys.path.insert(0, str(workspace))
    try:
        with ExitStack() as stack:
            blocked_connect = stack.enter_context(
                patch("psycopg2.connect", side_effect=ValidationError("database access is forbidden"))
            )
            classical = importlib.import_module("crawlers.classical")
            stack.enter_context(
                patch.object(classical, "upload_concerts", side_effect=ValidationError("upload is forbidden"))
            )
            stack.enter_context(
                patch.object(classical, "upload_potential_concerts", side_effect=ValidationError("upload is forbidden"))
            )
            module = importlib.import_module(module_name)
            from crawlers.base import BaseCrawler

            classes = [
                value
                for value in vars(module).values()
                if isinstance(value, type)
                and issubclass(value, BaseCrawler)
                and value is not BaseCrawler
                and value.__module__ == module.__name__
            ]
            if len(classes) != 1:
                raise ValidationError("main.py must define exactly one BaseCrawler subclass")
            crawler = classes[0]()
            if crawler.config.country_code != country_code.upper():
                raise ValidationError("CrawlerConfig.country_code does not match")
            if url is not None and crawler.config.source_url.rstrip("/") != url.rstrip("/"):
                raise ValidationError("CrawlerConfig.source_url does not match the requested URL")
            records = crawler.scrape()
            if blocked_connect.called:
                raise ValidationError("crawler attempted database access")
    finally:
        sys.path.remove(str(workspace))

    if not isinstance(records, list):
        raise ValidationError("scrape() must return a list")
    if not records:
        return {"status": "empty", "record_count": 0}
    normalized = _validate_records(records, crawler, url or crawler.config.source_url, country_code)
    return {
        "status": "passed",
        "record_count": len(normalized),
        "source_url": crawler.config.source_url,
    }


def validate(
    workspace: Path,
    crawler_directory: str,
    url: str | None,
    country_code: str,
    attempted_on: date | None = None,
) -> dict:
    workspace = workspace.resolve()
    crawler_dir = (workspace / crawler_directory).resolve()
    if workspace not in crawler_dir.parents:
        raise ValidationError("crawler directory escapes the workspace")
    main_path = crawler_dir / "main.py"
    blocked_path = crawler_dir / "BLOCKED.md"
    if main_path.exists() == blocked_path.exists():
        raise ValidationError("crawler directory must contain exactly one of main.py or BLOCKED.md")
    if blocked_path.exists():
        return validate_blocked(blocked_path, url, country_code, attempted_on)
    return validate_crawler(workspace, crawler_dir, url, country_code)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a generated crawler without uploading data.")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--crawler", required=True)
    parser.add_argument("--url")
    parser.add_argument("--country-code", required=True)
    parser.add_argument("--attempted-on", type=date.fromisoformat)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = datetime.now()
    try:
        result = validate(
            args.workspace,
            args.crawler,
            args.url,
            args.country_code,
            args.attempted_on,
        )
    except Exception as exc:
        result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        exit_code = 1
    else:
        exit_code = 2 if result["status"] == "empty" else 0
    result["duration_seconds"] = (datetime.now() - started).total_seconds()
    payload = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
