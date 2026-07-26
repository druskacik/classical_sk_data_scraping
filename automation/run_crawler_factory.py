from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from build_crawlers_with_codex import MODEL, URLS, country_code_for_url, crawler_folder_name
from automation.validate_generated_crawler import (
    ALLOWED_REASON_CODES,
    METADATA_PATTERN,
    parse_blocked_metadata,
)


DEFAULT_STATE_PATH = Path("/var/lib/crawler-factory/state.json")
DEFAULT_RUNS_DIR = Path("/var/lib/crawler-factory/runs")
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


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"urls": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"urls": {}}
    if not isinstance(state.get("urls"), dict):
        return {"urls": {}}
    return state


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def crawler_directory(url: str) -> Path:
    return Path("crawlers") / country_code_for_url(url).lower() / crawler_folder_name(url)


def is_due(url: str, workspace: Path, state: dict, today: date) -> bool:
    directory = workspace / crawler_directory(url)
    if (directory / "main.py").exists():
        return False
    blocked = directory / "BLOCKED.md"
    if blocked.exists():
        try:
            retry_after = date.fromisoformat(parse_blocked_metadata(blocked)["retry_after"])
        except Exception:
            return True
        return retry_after <= today
    entry = state["urls"].get(url, {})
    next_attempt = entry.get("next_attempt_at")
    return not next_attempt or date.fromisoformat(next_attempt) <= today


def select_urls(urls: list[str], workspace: Path, state: dict, today: date, limit: int) -> list[str]:
    return [url for url in urls if is_due(url, workspace, state, today)][:limit]


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


def write_empty_blocked(workspace: Path, url: str, country_code: str) -> None:
    directory = workspace / crawler_directory(url)
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    attempted = datetime.now(timezone.utc).date()
    metadata = {
        "url": url,
        "country_code": country_code,
        "reason_code": "no_current_events",
        "attempted_at": attempted.isoformat(),
        "retry_after": (attempted + timedelta(days=30)).isoformat(),
    }
    content = (
        "<!-- crawler-factory-metadata\n"
        f"{json.dumps(metadata, separators=(',', ':'))}\n"
        "-->\n\n"
        "# Temporarily blocked: no current concerts\n\n"
        f"Source investigated: {url}\n\n"
        "The crawler factory found a structurally usable source, but its live scrape returned "
        "zero concerts on the attempt date. This is treated as a seasonal or temporarily empty "
        "source rather than merged as an unverified crawler.\n\n"
        "Codex investigated the site's public API/network responses and its rendered HTML. "
        "No current concert records were available to validate required fields, pagination, "
        "detail-page extraction, or programme descriptions against real data.\n\n"
        "A later run should repeat both the API/network and HTML investigation. Publication of "
        "the venue's next season, or any live event containing a title, date, venue, URL, and "
        "programme description, would unblock implementation and end-to-end validation.\n"
    )
    (directory / "BLOCKED.md").write_text(content, encoding="utf-8")


def normalize_blocked_metadata(
    workspace: Path,
    url: str,
    country_code: str,
    attempted: date,
) -> None:
    path = workspace / crawler_directory(url) / "BLOCKED.md"
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


def git_commit(workspace: Path, url: str, status: str) -> str:
    directory = crawler_directory(url)
    run_command(["git", "add", "--", str(directory)], cwd=workspace)
    subject = (
        f"Add crawler for {crawler_folder_name(url)}"
        if status == "generated"
        else f"Record blocked crawler for {crawler_folder_name(url)}"
    )
    run_command(["git", "commit", "-m", subject], cwd=workspace)
    return run_command(["git", "rev-parse", "HEAD"], cwd=workspace).stdout.strip()


def attempt_url(
    workspace: Path,
    run_dir: Path,
    url: str,
    timeout_minutes: int,
    validation_timeout_seconds: int,
    child_env: dict[str, str],
) -> dict:
    started = time.monotonic()
    slug = crawler_folder_name(url)
    builder_report = run_dir / f"{slug}-builder.json"
    validator_report = run_dir / f"{slug}-validator.json"
    result = {
        "url": url,
        "crawler_directory": str(crawler_directory(url)),
        "status": "generation_failed",
        "validation": None,
        "commit": None,
        "error": None,
    }
    command = [
        sys.executable,
        str(workspace / "build_crawlers_with_codex.py"),
        "--workspace",
        str(workspace),
        "--url",
        url,
        "--retry-blocked",
        "--sandbox",
        "workspace-write",
        "--results",
        str(builder_report),
    ]
    try:
        generation = run_command(
            command,
            cwd=workspace,
            env=child_env,
            timeout=timeout_minutes * 60,
            check=False,
        )
        (run_dir / f"{slug}-builder.log").write_text(
            generation.stdout + generation.stderr,
            encoding="utf-8",
        )
        if generation.returncode != 0:
            result["error"] = f"builder exited with status {generation.returncode}"
            return result
        validate_change_scope(workspace, crawler_directory(url))
        normalize_blocked_metadata(
            workspace,
            url,
            country_code_for_url(url),
            datetime.now(timezone.utc).date(),
        )
        try:
            validation = run_command(
                [
                    sys.executable,
                    str(workspace / "automation/validate_generated_crawler.py"),
                    "--workspace",
                    str(workspace),
                    "--crawler",
                    str(crawler_directory(url)),
                    "--url",
                    url,
                    "--country-code",
                    country_code_for_url(url),
                    "--output",
                    str(validator_report),
                ],
                cwd=workspace,
                env=child_env,
                timeout=validation_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            result["status"] = "validation_failed"
            result["error"] = (
                f"crawler validation exceeded {validation_timeout_seconds} seconds"
            )
            return result
        (run_dir / f"{slug}-validator.log").write_text(
            validation.stdout + validation.stderr,
            encoding="utf-8",
        )
        validation_data = json.loads(validator_report.read_text(encoding="utf-8"))
        if validation_data["status"] == "empty":
            write_empty_blocked(workspace, url, country_code_for_url(url))
            validate_change_scope(workspace, crawler_directory(url))
            validation_data = {"status": "blocked", "converted_from_empty": True}
        elif validation.returncode != 0:
            result["status"] = "validation_failed"
            result["validation"] = validation_data
            result["error"] = validation_data.get("error")
            return result
        result["status"] = "blocked" if validation_data["status"] == "blocked" else "generated"
        result["validation"] = validation_data
        result["commit"] = git_commit(workspace, url, result["status"])
        return result
    except subprocess.TimeoutExpired:
        result["status"] = "timed_out"
        result["error"] = f"attempt exceeded {timeout_minutes} minutes"
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


def pr_body(results: list[dict], model: str, run_id: str) -> str:
    rows = ["| URL | Result | Validation | Directory |", "|---|---|---|---|"]
    for result in results:
        validation = result.get("validation") or {}
        rows.append(
            f"| {result['url']} | {result['status']} | "
            f"{validation.get('status', 'n/a')} | `{result['crawler_directory']}` |"
        )
    return "\n".join(
        [
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
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and publish a daily crawler batch.")
    parser.add_argument("--repository", required=True, help="Git clone URL")
    parser.add_argument("--base-branch", default="master")
    parser.add_argument("--max-urls", type=int, default=5)
    parser.add_argument("--timeout-minutes", type=int, default=30)
    parser.add_argument("--validation-timeout-seconds", type=int, default=300)
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument("--url", action="append", dest="urls")
    parser.add_argument("--no-push", action="store_true", help="Generate commits but do not push or open a PR")
    parser.add_argument("--keep-workspace", action="store_true")
    return parser.parse_args()


def run_factory(args: argparse.Namespace) -> None:
    if args.max_urls < 1:
        raise SystemExit("--max-urls must be at least 1")
    if args.validation_timeout_seconds < 1:
        raise SystemExit("--validation-timeout-seconds must be at least 1")
    run_id = f"{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:8]}"
    run_dir = args.runs_dir / run_id
    run_dir.mkdir(parents=True)
    workspace = Path(tempfile.mkdtemp(prefix="crawler-factory-"))
    state = load_state(args.state_path)
    today = datetime.now(timezone.utc).date()
    results = []
    try:
        if os.getenv("GH_TOKEN"):
            run_command(["gh", "auth", "setup-git"])
        run_command(
            ["git", "clone", "--single-branch", "--branch", args.base_branch, args.repository, str(workspace)]
        )
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
        candidates = select_urls(args.urls or URLS, workspace, state, today, args.max_urls)
        child_env = sanitized_child_env(run_dir)
        for url in candidates:
            result = attempt_url(
                workspace,
                run_dir,
                url,
                args.timeout_minutes,
                args.validation_timeout_seconds,
                child_env,
            )
            results.append(result)
            if result["status"] in {"generated", "blocked"}:
                state["urls"].pop(url, None)
            else:
                reset_failed_attempt(workspace)
                state["urls"][url] = {
                    "status": result["status"],
                    "last_attempt_at": today.isoformat(),
                    "next_attempt_at": (today + timedelta(days=7)).isoformat(),
                    "error": result.get("error"),
                }
            save_state(args.state_path, state)

        report_path = run_dir / "batch-report.json"
        report_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        successful = [item for item in results if item["status"] in {"generated", "blocked"}]
        if not successful:
            print(f"No validated changes; report: {report_path}")
            return
        if args.no_push:
            print(f"Created {len(successful)} commits in {workspace}; report: {report_path}")
            args.keep_workspace = True
            return

        run_command(["git", "push", "--set-upstream", "origin", branch], cwd=workspace)
        body_path = run_dir / "pull-request.md"
        body_path.write_text(pr_body(results, MODEL, run_id), encoding="utf-8")
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
        run_command(
            ["gh", "pr", "merge", pr_url, "--auto", "--squash", "--delete-branch"],
            cwd=workspace,
        )
        print(f"Opened auto-merge PR: {pr_url}")
    finally:
        if not args.keep_workspace:
            shutil.rmtree(workspace, ignore_errors=True)


def main() -> None:
    args = parse_args()
    lock_path = args.state_path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another crawler-factory run is already active")
        run_factory(args)


if __name__ == "__main__":
    main()
