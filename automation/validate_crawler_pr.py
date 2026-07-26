from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


class PullRequestValidationError(RuntimeError):
    pass


def command(args: list[str], cwd: Path, timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def changed_files(workspace: Path, base_ref: str) -> list[tuple[str, str]]:
    result = command(["git", "diff", "--name-status", f"{base_ref}...HEAD"], workspace)
    if result.returncode:
        raise PullRequestValidationError(result.stderr.strip())
    changes = []
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        status = fields[0]
        path = fields[-1]
        changes.append((status, path))
    return changes


def generated_directories(workspace: Path, base_ref: str) -> list[Path]:
    changes = changed_files(workspace, base_ref)
    if not changes:
        raise PullRequestValidationError("PR has no changes")
    directories = set()
    for status, raw_path in changes:
        path = Path(raw_path)
        if (
            len(path.parts) != 4
            or path.parts[0] != "crawlers"
            or len(path.parts[1]) != 2
            or path.name not in {"main.py", "BLOCKED.md"}
        ):
            raise PullRequestValidationError(f"change outside generated crawler scope: {raw_path}")
        if status[0] not in {"A", "D", "M"}:
            raise PullRequestValidationError(f"unsupported change status {status}: {raw_path}")
        candidate = workspace / path
        if candidate.exists():
            if candidate.stat().st_size > 256_000:
                raise PullRequestValidationError(f"generated file is larger than 256 KB: {raw_path}")
            text = candidate.read_text(encoding="utf-8")
            secret_patterns = (
                r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
                r"\bgh[opsu]_[A-Za-z0-9]{20,}\b",
                r"\bsk-[A-Za-z0-9_-]{20,}\b",
            )
            if any(re.search(pattern, text) for pattern in secret_patterns):
                raise PullRequestValidationError(f"possible secret in generated file: {raw_path}")
        directories.add(path.parent)

    for directory in directories:
        base_main = command(
            ["git", "cat-file", "-e", f"{base_ref}:{directory}/main.py"],
            workspace,
        ).returncode == 0
        if base_main:
            raise PullRequestValidationError(f"existing crawler may not be modified: {directory}")
    return sorted(directories)


def is_transient_failure(payload: dict) -> bool:
    error = payload.get("error", "").lower()
    return any(
        marker in error
        for marker in (
            "connectionerror",
            "connecttimeout",
            "readtimeout",
            "requestexception",
            "temporarily unavailable",
            "timed out",
            "timeout",
        )
    )


def validate_directory(workspace: Path, directory: Path, timeout_seconds: int) -> dict:
    output = workspace / ".crawler-validation.json"
    validator_command = [
        sys.executable,
        "automation/validate_generated_crawler.py",
        "--workspace",
        str(workspace),
        "--crawler",
        str(directory),
        "--country-code",
        directory.parts[1].upper(),
        "--output",
        str(output),
    ]
    result = None
    payload = {}
    for attempt in range(2):
        result = command(validator_command, workspace, timeout=timeout_seconds)
        if output.exists():
            payload = json.loads(output.read_text(encoding="utf-8"))
        if result.returncode == 0:
            break
        if attempt == 0 and is_transient_failure(payload):
            continue
        break
    assert result is not None
    try:
        if not payload:
            payload = json.loads(output.read_text(encoding="utf-8"))
    finally:
        output.unlink(missing_ok=True)
    if result.returncode:
        raise PullRequestValidationError(
            f"{directory} failed validation: {payload.get('error', result.stderr.strip())}"
        )
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate an automated crawler-factory PR.")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--base-ref", default="origin/master")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace = args.workspace.resolve()
    results = {}
    try:
        for directory in generated_directories(workspace, args.base_ref):
            results[str(directory)] = validate_directory(
                workspace,
                directory,
                args.timeout_seconds,
            )
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}, indent=2))
        raise SystemExit(1)
    print(json.dumps({"status": "passed", "crawlers": results}, indent=2))


if __name__ == "__main__":
    main()
