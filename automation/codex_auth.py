from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


REVOKED_REFRESH_TOKEN_MESSAGE = (
    "access token could not be refreshed because your refresh token was revoked"
)


class CodexAuthRequiredError(RuntimeError):
    def __init__(self, reason_code: str, message: str = "Codex authentication is required") -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _exception_messages(error: BaseException) -> Iterable[str]:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield str(current)
        current = current.__cause__ or current.__context__


def codex_auth_reason(value: str | BaseException | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, CodexAuthRequiredError):
        return value.reason_code
    messages = _exception_messages(value) if isinstance(value, BaseException) else (value,)
    for message in messages:
        normalized = message.casefold()
        if REVOKED_REFRESH_TOKEN_MESSAGE in normalized:
            return "refresh_token_revoked"
        if "missing bearer or basic authentication" in normalized:
            return "missing_api_auth"
        if "not logged in" in normalized:
            return "login_required"
        if (
            ("401 unauthorized" in normalized or "status 401" in normalized)
            and ("api.openai.com" in normalized or "codex" in normalized)
        ):
            return "openai_unauthorized"
    return None


def raise_for_codex_auth(value: str | BaseException | None) -> None:
    reason = codex_auth_reason(value)
    if reason:
        raise CodexAuthRequiredError(reason)


def default_pause_path(service: str) -> Path:
    if service == "classical-crawler-factory":
        return Path("/var/lib/crawler-factory/codex-auth-required.json")
    return Path("/var/lib/classical-bot/codex-auth-required.json")


def auth_file_signature() -> dict[str, int] | None:
    auth_path = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"
    try:
        stat = auth_path.stat()
    except OSError:
        return None
    return {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}


@dataclass
class CodexAuthPause:
    service: str
    path: Path

    @classmethod
    def for_service(cls, service: str, path: Path | None = None) -> CodexAuthPause:
        return cls(service, path or default_pause_path(service))

    def load(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def pause(self, reason_code: str, context: dict[str, Any] | None = None) -> bool:
        if self.path.exists():
            return False
        payload = {
            "version": 1,
            "service": self.service,
            "detected_at": datetime.now(UTC).isoformat(),
            "reason_code": reason_code,
            "context": context or {},
            "auth_file_signature": auth_file_signature(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.path)
        return True

    def is_paused(self) -> bool:
        return self.path.exists()

    def auth_file_changed(self) -> bool:
        state = self.load()
        return state is not None and state.get("auth_file_signature") != auth_file_signature()

    def verify_and_resume(
        self,
        *,
        cwd: Path,
        timeout_seconds: int = 120,
        force: bool = False,
    ) -> tuple[bool, str | None]:
        if not self.is_paused():
            return True, None
        if not force and not self.auth_file_changed():
            return False, None
        try:
            result = subprocess.run(
                ["codex", "exec", "--json", "Reply with exactly OK."],
                cwd=cwd,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return False, type(error).__name__
        combined = f"{result.stdout}\n{result.stderr}"
        reason = codex_auth_reason(combined)
        if result.returncode == 0:
            self.path.unlink(missing_ok=True)
            return True, None
        if reason:
            state = self.load() or {}
            state["auth_file_signature"] = auth_file_signature()
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            temporary.replace(self.path)
        return False, reason or "smoke_test_failed"


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect or resume a Codex authentication pause.")
    parser.add_argument("command", choices=("status", "resume"))
    parser.add_argument(
        "--service",
        choices=("classical-bot", "classical-crawler-factory"),
        default=os.getenv("LOG_SERVICE", "classical-bot"),
    )
    parser.add_argument("--state-path", type=Path)
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    args = parser.parse_args()
    pause = CodexAuthPause.for_service(args.service, args.state_path)
    state = pause.load()
    if args.command == "status":
        if not pause.is_paused():
            print("Codex authentication is not paused")
            return
        if state is None:
            print("Codex authentication is paused (state file is unreadable)")
            return
        print(
            "Codex authentication is paused "
            f"(reason={state.get('reason_code', 'unknown')}, "
            f"detected_at={state.get('detected_at', 'unknown')})"
        )
        return
    if not pause.is_paused():
        print("Codex authentication is not paused")
        return
    resumed, failure = pause.verify_and_resume(cwd=args.cwd, force=True)
    if not resumed:
        raise SystemExit(f"Codex authentication verification failed: {failure or 'unknown'}")
    print("Codex authentication verified; pause cleared")


if __name__ == "__main__":
    main()
