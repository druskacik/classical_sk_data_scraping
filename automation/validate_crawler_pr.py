from __future__ import annotations

import argparse
import json
import re
import subprocess
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
            try:
                text = candidate.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise PullRequestValidationError(
                    f"generated file is not valid UTF-8: {raw_path}"
                ) from exc
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
        main_path = workspace / directory / "main.py"
        blocked_path = workspace / directory / "BLOCKED.md"
        if main_path.exists() == blocked_path.exists():
            raise PullRequestValidationError(
                f"{directory} must contain exactly one of main.py or BLOCKED.md"
            )
    return sorted(directories)


def validate_directory(workspace: Path, directory: Path) -> dict:
    main_path = workspace / directory / "main.py"
    if not main_path.exists():
        return {"status": "passed", "kind": "blocked"}
    source = main_path.read_text(encoding="utf-8")
    try:
        compile(source, str(main_path), "exec")
    except SyntaxError as exc:
        raise PullRequestValidationError(
            f"{directory} has invalid Python syntax: {exc}"
        ) from exc
    return {"status": "passed", "kind": "crawler"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate an automated crawler-factory PR.")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--base-ref", default="origin/master")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace = args.workspace.resolve()
    results = {}
    try:
        for directory in generated_directories(workspace, args.base_ref):
            results[str(directory)] = validate_directory(workspace, directory)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}, indent=2))
        raise SystemExit(1)
    print(json.dumps({"status": "passed", "crawlers": results}, indent=2))


if __name__ == "__main__":
    main()
