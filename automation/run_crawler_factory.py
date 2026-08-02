from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from automation.crawler_registry import CrawlerRegistry
from build_crawlers_with_codex import MODEL, country_code_for_url, crawler_folder_name


DEFAULT_LOCK_PATH = Path("/var/lib/crawler-factory/factory.lock")
DEFAULT_RUNS_DIR = Path("/var/lib/crawler-factory/runs")
DEFAULT_VALIDATION_TIMEOUT_MINUTES = 15
METADATA_PATTERN = re.compile(
    r"<!-- crawler-factory-metadata\s*(\{.*?\})\s*-->",
    re.DOTALL,
)
ALLOWED_REASON_CODES = {
    "no_current_events",
    "access_blocked",
    "no_parseable_source",
    "implementation_failed",
}
ALLOWED_CHILD_ENV = {
    "CODEX_BIN",
    "CODEX_HOME",
    "HOME",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "OPENAI_API_KEY",
    "PATH",
    "PLAYWRIGHT_MCP_EXECUTABLE_PATH",
    "PLAYWRIGHT_MCP_HEADLESS",
    "PLAYWRIGHT_MCP_NO_SANDBOX",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TZ",
}


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        timeout=timeout,
        check=check,
        text=True,
        capture_output=True,
    )


def crawler_directory(url: str, country_code: str | None = None) -> Path:
    country = country_code or country_code_for_url(url)
    return Path("crawlers") / country.lower() / crawler_folder_name(url)


def source_directory(source: dict) -> Path:
    if not source.get("crawler_path"):
        raise ValueError(f"Crawler source {source['id']} has no assigned crawler_path")
    path = Path(source["crawler_path"])
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Crawler source {source['id']} has an unsafe crawler_path")
    return path


def parse_blocked_metadata(path: Path) -> dict:
    match = METADATA_PATTERN.search(path.read_text(encoding="utf-8"))
    if not match:
        raise ValueError("BLOCKED.md has no crawler-factory metadata block")
    metadata = json.loads(match.group(1))
    required = {"url", "country_code", "reason_code", "attempted_at", "retry_after"}
    if not required.issubset(metadata):
        raise ValueError("BLOCKED.md metadata is incomplete")
    return metadata


def resolve_source_url(url: str, timeout_seconds: int = 20) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; ClassicalCrawlerFactory/1.0)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.geturl()
    except Exception:
        return url


def sanitized_child_env(run_dir: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key in ALLOWED_CHILD_ENV}
    child_home = Path(env.get("CODEX_HOME") or run_dir / "codex-home")
    child_home.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(child_home)
    env["CODEX_HOME"] = str(child_home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def changed_paths(workspace: Path) -> list[str]:
    result = run_command(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=workspace,
    )
    paths = []
    entries = result.stdout.split("\0")
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        status = entry[:2]
        path = entry[3:]
        if status[0] in {"R", "C"} and index < len(entries):
            path = entries[index]
            index += 1
        paths.append(path)
    return paths


def validate_change_scope(workspace: Path, expected_directory: Path) -> None:
    allowed = {
        str(expected_directory / "main.py"),
        str(expected_directory / "BLOCKED.md"),
    }
    paths = set(changed_paths(workspace))
    unexpected = sorted(paths - allowed)
    if not paths:
        raise RuntimeError("Codex produced no repository changes")
    if unexpected:
        raise RuntimeError(f"Codex changed files outside the allowed scope: {unexpected}")
    main_path = workspace / expected_directory / "main.py"
    blocked_path = workspace / expected_directory / "BLOCKED.md"
    if main_path.exists() == blocked_path.exists():
        raise RuntimeError("Codex must produce exactly one of main.py or BLOCKED.md")


def normalize_blocked_metadata(
    workspace: Path,
    url: str,
    country_code: str,
    crawler_path: Path,
    attempted: date,
) -> None:
    path = workspace / crawler_path / "BLOCKED.md"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    match = METADATA_PATTERN.search(text)
    existing = {}
    if match:
        try:
            existing = json.loads(match.group(1))
        except json.JSONDecodeError:
            existing = {}
    reason_code = existing.get("reason_code", "implementation_failed")
    if reason_code not in ALLOWED_REASON_CODES:
        reason_code = "implementation_failed"
    metadata = {
        "url": url,
        "country_code": country_code.upper(),
        "reason_code": reason_code,
        "attempted_at": attempted.isoformat(),
        "retry_after": (attempted + timedelta(days=30)).isoformat(),
    }
    block = (
        "<!-- crawler-factory-metadata\n"
        f"{json.dumps(metadata, separators=(',', ':'))}\n"
        "-->"
    )
    if match:
        text = f"{text[:match.start()]}{block}{text[match.end():]}"
    else:
        text = f"{block}\n\n{text}"
    path.write_text(text, encoding="utf-8")


def git_commit(workspace: Path, source: dict, status: str) -> str:
    directory = source_directory(source)
    run_command(["git", "add", "--", str(directory)], cwd=workspace)
    slug = directory.name
    subject = (
        f"Add crawler for {slug}"
        if status == "generated"
        else f"Record blocked crawler for {slug}"
    )
    run_command(["git", "commit", "-m", subject], cwd=workspace)
    return run_command(["git", "rev-parse", "HEAD"], cwd=workspace).stdout.strip()


def validate_generated_crawler(
    workspace: Path,
    run_dir: Path,
    source: dict,
    crawler_path: Path,
    child_env: dict[str, str],
    timeout_minutes: int,
) -> dict:
    slug = crawler_path.name
    report_path = run_dir / f"{source['id']}-{slug}-validation.json"
    command = [
        sys.executable,
        "-m",
        "automation.validate_generated_crawler",
        "--crawler-directory",
        str(crawler_path),
    ]
    try:
        completed = run_command(
            command,
            cwd=workspace,
            env=child_env,
            timeout=timeout_minutes * 60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        report = {
            "status": "inconclusive_runtime",
            "record_count": None,
            "issue_count": None,
            "issues": [],
            "error": f"full scrape exceeded {timeout_minutes} minutes",
            "duration_seconds": timeout_minutes * 60,
        }
    else:
        try:
            report = json.loads(completed.stdout)
        except (json.JSONDecodeError, TypeError):
            report = {
                "status": "execution_error",
                "record_count": None,
                "issue_count": None,
                "issues": [],
                "error": "validator did not return valid JSON",
                "duration_seconds": None,
            }
        if not isinstance(report, dict):
            report = {
                "status": "execution_error",
                "record_count": None,
                "issue_count": None,
                "issues": [],
                "error": "validator JSON must be an object",
                "duration_seconds": None,
            }
        if completed.returncode != 0 and report.get("status") == "passed":
            report["status"] = "execution_error"
            report["error"] = "validator exited unsuccessfully after reporting success"
        if completed.stderr.strip():
            report["validator_stderr"] = completed.stderr.strip()[:1000]
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def validation_error(report: dict) -> str:
    status = report.get("status", "execution_error")
    if status == "data_quality_failure":
        count = report.get("issue_count")
        examples = report.get("issues") or []
        detail = "; ".join(
            f"record {issue.get('record')} {issue.get('field')}: {issue.get('reason')}"
            for issue in examples[:3]
        )
        return f"live validation found {count} data-quality issue(s): {detail}"
    return f"live validation {status}: {report.get('error') or 'unknown error'}"


def attempt_source(
    workspace: Path,
    run_dir: Path,
    source: dict,
    timeout_minutes: int,
    child_env: dict[str, str],
    model: str = MODEL,
    validation_timeout_minutes: int = DEFAULT_VALIDATION_TIMEOUT_MINUTES,
) -> dict:
    started = time.monotonic()
    url = source["canonical_url"]
    directory = source_directory(source)
    slug = directory.name
    builder_report = run_dir / f"{source['id']}-{slug}-builder.json"
    result = {
        "source_id": source["id"],
        "url": url,
        "resolved_url": url,
        "crawler_directory": str(directory),
        "status": "generation_failed",
        "commit": None,
        "generation_warning": None,
        "validation_status": None,
        "validation_record_count": None,
        "validation_duration_seconds": None,
        "final_response": None,
        "error": None,
    }
    command = [
        sys.executable,
        str(workspace / "build_crawlers_with_codex.py"),
        "--workspace",
        str(workspace),
        "--model",
        model,
        "--url",
        url,
        "--country-code",
        source["country_code"],
        "--crawler-directory",
        str(directory),
        "--retry-blocked",
        "--sandbox",
        "full-access",
        "--results",
        str(builder_report),
    ]
    try:
        try:
            generation = run_command(
                command,
                cwd=workspace,
                env=child_env,
                timeout=timeout_minutes * 60,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            output_parts = []
            for part in (exc.stdout, exc.stderr):
                if isinstance(part, bytes):
                    part = part.decode(errors="replace")
                if part:
                    output_parts.append(part)
            (run_dir / f"{source['id']}-{slug}-builder.log").write_text(
                "".join(output_parts),
                encoding="utf-8",
            )
            unit = "minute" if timeout_minutes == 1 else "minutes"
            result["generation_warning"] = f"builder exceeded {timeout_minutes} {unit}"
        else:
            (run_dir / f"{source['id']}-{slug}-builder.log").write_text(
                generation.stdout + generation.stderr,
                encoding="utf-8",
            )
            if generation.returncode != 0:
                result["generation_warning"] = (
                    f"builder exited with status {generation.returncode}"
                )
        validate_change_scope(workspace, directory)
        normalize_blocked_metadata(
            workspace,
            url,
            source["country_code"],
            directory,
            datetime.now(UTC).date(),
        )
        output_path = workspace / directory
        blocked = (output_path / "BLOCKED.md").exists()
        if builder_report.exists():
            try:
                builder_data = json.loads(builder_report.read_text(encoding="utf-8"))
                if builder_data:
                    result["final_response"] = builder_data[-1].get("final_response")
            except (json.JSONDecodeError, OSError, TypeError, AttributeError) as exc:
                warning = f"builder report could not be read: {type(exc).__name__}"
                result["generation_warning"] = (
                    f"{result['generation_warning']}; {warning}"
                    if result["generation_warning"]
                    else warning
                )
        if blocked:
            result["status"] = "blocked"
            result["validation_status"] = "not_applicable"
        else:
            validation = validate_generated_crawler(
                workspace,
                run_dir,
                source,
                directory,
                child_env,
                validation_timeout_minutes,
            )
            result["validation_status"] = validation.get("status")
            result["validation_record_count"] = validation.get("record_count")
            result["validation_duration_seconds"] = validation.get("duration_seconds")
            if validation.get("status") != "passed":
                result["error"] = validation_error(validation)
                return result
            result["status"] = "generated"
        result["commit"] = git_commit(workspace, source, result["status"])
        return result
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    finally:
        result["duration_seconds"] = round(time.monotonic() - started, 2)


def reset_failed_attempt(workspace: Path) -> None:
    paths = changed_paths(workspace)
    if paths:
        run_command(["git", "clean", "-fd"], cwd=workspace)
        run_command(["git", "restore", "--worktree", "--staged", "."], cwd=workspace)


def reconcile_pull_requests(registry: CrawlerRegistry, workspace: Path) -> dict[str, int]:
    grouped: dict[str, list[dict]] = {}
    for source in registry.pr_open_sources():
        grouped.setdefault(source["pull_request_url"], []).append(source)
    counts = {"retry_wait": 0, "needs_attention": 0}
    terminal_failures = {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"}
    for pr_url, sources in grouped.items():
        try:
            result = run_command(
                [
                    "gh",
                    "pr",
                    "view",
                    pr_url,
                    "--json",
                    "state,mergedAt,statusCheckRollup",
                ],
                cwd=workspace,
                timeout=30,
            )
            data = json.loads(result.stdout)
        except Exception as exc:
            print(f"Could not reconcile {pr_url}: {type(exc).__name__}: {exc}")
            continue
        ids = [source["id"] for source in sources]
        if data.get("state") == "CLOSED" and not data.get("mergedAt"):
            registry.transition_sources(
                ids,
                "retry_wait",
                retry_after=datetime.now(UTC) + timedelta(days=7),
            )
            counts["retry_wait"] += len(ids)
            continue
        conclusions = {
            str(check.get("conclusion") or check.get("state") or "").upper()
            for check in data.get("statusCheckRollup") or []
        }
        if conclusions & terminal_failures:
            registry.transition_sources(ids, "needs_attention")
            counts["needs_attention"] += len(ids)
    return counts


def pr_body(results: list[dict], model: str, run_id: str) -> str:
    rows = [
        "| Source | URL | Result | Validation | Warning | Directory |",
        "|---|---|---|---|---|---|",
    ]
    for result in results:
        validation = result.get("validation_status") or "not run"
        if validation == "passed":
            validation = (
                f"passed ({result.get('validation_record_count')} records, "
                f"{result.get('validation_duration_seconds')}s)"
            )
        rows.append(
            f"| {result['source_id']} | {result['url']} | {result['status']} | {validation} | "
            f"{result.get('generation_warning') or 'none'} | "
            f"`{result['crawler_directory']}` |"
        )
    sections = [
        "Automated crawler-factory batch.",
        "",
        *rows,
        "",
        f"- Run ID: `{run_id}`",
        f"- Model: `{model}`",
        f"- Total attempts: {len(results)}",
        "",
        "Detailed sanitized logs and JSON reports are retained by the worker.",
    ]
    for result in results:
        if result.get("final_response"):
            sections.extend(
                [
                    "",
                    f"<details><summary>Codex report for {result['url']}</summary>",
                    "",
                    result["final_response"],
                    "",
                    "</details>",
                ]
            )
    return "\n".join(sections)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and publish a database-backed crawler batch.")
    parser.add_argument(
        "--repository",
        default=os.getenv("CRAWLER_FACTORY_REPOSITORY"),
        help=(
            "Git clone URL. Defaults to the CRAWLER_FACTORY_REPOSITORY "
            "environment variable."
        ),
    )
    parser.add_argument("--base-branch", default="master")
    parser.add_argument("--max-urls", type=int, default=5)
    parser.add_argument(
        "--model",
        default=MODEL,
        help=f"Codex model for this batch (default: {MODEL}).",
    )
    parser.add_argument("--timeout-minutes", type=int, default=60)
    parser.add_argument(
        "--validation-timeout-minutes",
        type=int,
        default=int(
            os.getenv(
                "CRAWLER_FACTORY_VALIDATION_TIMEOUT_MINUTES",
                str(DEFAULT_VALIDATION_TIMEOUT_MINUTES),
            )
        ),
        help="Maximum minutes for the authoritative full scrape (default: 15).",
    )
    parser.add_argument("--lock-path", type=Path, default=DEFAULT_LOCK_PATH)
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument("--url", action="append", dest="urls")
    parser.add_argument("--country-code")
    parser.add_argument("--source-id", action="append", type=int, dest="source_ids")
    parser.add_argument("--no-push", action="store_true", help="Generate commits but do not push or open a PR")
    parser.add_argument("--keep-workspace", action="store_true")
    args = parser.parse_args()
    if not args.repository:
        parser.error(
            "--repository is required when CRAWLER_FACTORY_REPOSITORY is not set"
        )
    return args


def run_factory(args: argparse.Namespace, registry: CrawlerRegistry) -> None:
    if args.max_urls < 1:
        raise SystemExit("--max-urls must be at least 1")
    if args.validation_timeout_minutes < 1:
        raise SystemExit("--validation-timeout-minutes must be at least 1")
    if args.urls and args.source_ids:
        raise SystemExit("--url and --source-id are mutually exclusive")
    run_id = f"{datetime.now(UTC):%Y%m%d}-{uuid.uuid4().hex[:8]}"
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{run_id}"
    run_dir = args.runs_dir / run_id
    run_dir.mkdir(parents=True)
    workspace = Path(tempfile.mkdtemp(prefix="crawler-factory-"))
    results: list[dict] = []
    successful_source_ids: list[int] = []
    run_created = False
    try:
        if os.getenv("GH_TOKEN"):
            run_command(["gh", "auth", "setup-git"])
        run_command(
            ["git", "clone", "--single-branch", "--branch", args.base_branch, args.repository, str(workspace)]
        )
        today = datetime.now(UTC).date()
        branch = f"crawler-factory/{today.isoformat()}-{run_id[-8:]}"
        run_command(["git", "switch", "-c", branch], cwd=workspace)
        run_command(
            ["git", "config", "user.name", os.getenv("CRAWLER_FACTORY_GIT_NAME", "ClassicalBot")],
            cwd=workspace,
        )
        run_command(
            [
                "git",
                "config",
                "user.email",
                os.getenv("CRAWLER_FACTORY_GIT_EMAIL", "classicalbot@users.noreply.github.com"),
            ],
            cwd=workspace,
        )
        manual_ids = list(args.source_ids or [])
        if args.urls:
            for url in args.urls:
                country = args.country_code or country_code_for_url(url)
                source = registry.ingest_source(
                    url,
                    country,
                    discovered_by="factory_manual",
                )
                manual_ids.append(source["id"])
        registry.preflight()
        registry.recover_expired_leases()
        registry.reconcile_workspace(workspace)
        reconcile_pull_requests(registry, workspace)
        registry.reconcile_run_statuses()
        registry.create_run(run_id, worker_id, branch, args.model)
        run_created = True
        child_env = sanitized_child_env(run_dir)
        for _ in range(args.max_urls):
            source = registry.claim_next(
                worker_id,
                lease_minutes=args.timeout_minutes + 15,
                source_ids=manual_ids or None,
            )
            if not source:
                break
            attempt_id = registry.start_attempt(source, run_id)
            resolved_url = resolve_source_url(source["canonical_url"])
            assigned_path = source.get("crawler_path") or str(
                crawler_directory(resolved_url, source["country_code"])
            )
            source = registry.assign_resolved_identity(
                source["id"],
                resolved_url,
                assigned_path,
            )
            if source["status"] == "duplicate":
                result = {
                    "source_id": source["id"],
                    "url": source["canonical_url"],
                    "resolved_url": resolved_url,
                    "crawler_directory": assigned_path,
                    "status": "duplicate",
                    "commit": None,
                    "generation_warning": None,
                    "final_response": None,
                    "error": None,
                    "duration_seconds": 0,
                }
                registry.complete_attempt(
                    source["id"],
                    attempt_id,
                    "duplicate",
                    resolved_url=resolved_url,
                    crawler_path=assigned_path,
                )
                results.append(result)
                continue
            result = attempt_source(
                workspace,
                run_dir,
                source,
                args.timeout_minutes,
                child_env,
                args.model,
                args.validation_timeout_minutes,
            )
            result["resolved_url"] = resolved_url
            results.append(result)
            registry.complete_attempt(
                source["id"],
                attempt_id,
                result["status"],
                resolved_url=resolved_url,
                crawler_path=result["crawler_directory"],
                commit_sha=result.get("commit"),
                warning=result.get("generation_warning"),
                error=result.get("error"),
            )
            if result["status"] in {"generated", "blocked"}:
                successful_source_ids.append(source["id"])
            else:
                reset_failed_attempt(workspace)

        report_path = run_dir / "batch-report.json"
        report_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        if not successful_source_ids:
            registry.finish_run(run_id, "no_changes")
            print(f"No generated changes; report: {report_path}")
            return
        if args.no_push:
            registry.finish_run(run_id, "failed")
            print(f"Created {len(successful_source_ids)} commits in {workspace}; report: {report_path}")
            args.keep_workspace = True
            return

        run_command(["git", "push", "--set-upstream", "origin", branch], cwd=workspace)
        body_path = run_dir / "pull-request.md"
        body_path.write_text(pr_body(results, args.model, run_id), encoding="utf-8")
        pr_url = run_command(
            [
                "gh",
                "pr",
                "create",
                "--base",
                args.base_branch,
                "--head",
                branch,
                "--title",
                f"Automated crawler batch: {today.isoformat()}",
                "--body-file",
                str(body_path),
            ],
            cwd=workspace,
        ).stdout.strip()
        registry.mark_pr_open(run_id, successful_source_ids, pr_url)
        registry.finish_run(run_id, "pr_open", pull_request_url=pr_url)
        try:
            run_command(
                ["gh", "pr", "merge", pr_url, "--auto", "--squash", "--delete-branch"],
                cwd=workspace,
            )
        except Exception:
            registry.transition_sources(successful_source_ids, "needs_attention")
            raise
        print(f"Opened auto-merge PR: {pr_url}")
    except Exception:
        if run_created:
            registry.finish_run(run_id, "failed")
        raise
    finally:
        if not args.keep_workspace:
            shutil.rmtree(workspace, ignore_errors=True)


def main() -> None:
    args = parse_args()
    load_dotenv()
    args.lock_path.parent.mkdir(parents=True, exist_ok=True)
    with args.lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another crawler-factory run is already active")
        with CrawlerRegistry() as registry:
            run_factory(args, registry)


if __name__ == "__main__":
    main()
