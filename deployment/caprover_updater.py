from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable


COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DEPLOY_REQUEST_SUPPRESSION = timedelta(minutes=30)


def normalize_commit_sha(value: str, source: str) -> str:
    normalized = value.strip().lower()
    if not COMMIT_SHA_PATTERN.fullmatch(normalized):
        raise ValueError(f"{source} is not a 40-character hexadecimal commit SHA")
    return normalized


def remote_commit(repository: str, branch: str = "master") -> str:
    result = subprocess.run(
        ["git", "ls-remote", repository, f"refs/heads/{branch}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    fields = result.stdout.split()
    if len(fields) < 2:
        raise RuntimeError(f"Remote branch {branch!r} was not found")
    return normalize_commit_sha(fields[0], f"remote branch {branch!r}")


def request_deployment(webhook: str) -> None:
    request = urllib.request.Request(
        webhook,
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"CapRover webhook returned HTTP {response.status}")


def load_state(path: Path, log: Callable[[str], None]) -> dict:
    if not path.exists():
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log(f"Ignoring unreadable deployment state at {path}")
        return {}
    return state if isinstance(state, dict) else {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def recently_requested(state: dict, commit: str, now: datetime | None = None) -> bool:
    if state.get("last_deploy_request_sha") != commit:
        return False
    try:
        requested_at = datetime.fromisoformat(state["last_deploy_request_at"])
    except (KeyError, TypeError, ValueError):
        return False
    if requested_at.tzinfo is None:
        return False
    return (now or datetime.now(UTC)) - requested_at < DEPLOY_REQUEST_SUPPRESSION


@dataclass(frozen=True)
class CapRoverUpdaterConfig:
    repository: str
    deploy_webhook: str | None
    webhook_environment_name: str
    state_path: Path


class CapRoverUpdater:
    def __init__(self, config: CapRoverUpdaterConfig, log: Callable[[str], None]) -> None:
        self.config = config
        self.log = log
        self.state = load_state(config.state_path, log)
        self._configuration_warning_logged = False
        self.last_check_conclusive = False

    def save_state(self) -> None:
        save_state(self.config.state_path, self.state)

    def check_for_update(self, *, latest_commit: str | None = None) -> bool:
        self.last_check_conclusive = False
        deployed_commit_value = os.getenv("CAPROVER_GIT_COMMIT_SHA")
        if not self.config.deploy_webhook or not deployed_commit_value:
            if not self._configuration_warning_logged:
                missing = []
                if not self.config.deploy_webhook:
                    missing.append(self.config.webhook_environment_name)
                if not deployed_commit_value:
                    missing.append("CAPROVER_GIT_COMMIT_SHA")
                self.log(f"Automatic updates disabled; missing {', '.join(missing)}")
                self._configuration_warning_logged = True
            self.last_check_conclusive = True
            return False
        try:
            deployed_commit = normalize_commit_sha(
                deployed_commit_value,
                "CAPROVER_GIT_COMMIT_SHA",
            )
        except ValueError as exc:
            if not self._configuration_warning_logged:
                self.log(f"Automatic updates disabled; {exc}")
                self._configuration_warning_logged = True
            self.last_check_conclusive = True
            return False
        if latest_commit is None:
            try:
                latest_commit = remote_commit(self.config.repository)
            except Exception as exc:
                self.log(f"Could not check master for updates: {type(exc).__name__}: {exc}")
                return False
        else:
            try:
                latest_commit = normalize_commit_sha(latest_commit, "latest commit")
            except ValueError as exc:
                self.log(f"Could not check master for updates: {type(exc).__name__}: {exc}")
                return False
        if latest_commit == deployed_commit or recently_requested(self.state, latest_commit):
            self.last_check_conclusive = True
            return False
        try:
            request_deployment(self.config.deploy_webhook)
        except Exception as exc:
            self.log(f"Could not request CapRover deployment: {type(exc).__name__}: {exc}")
            return False
        self.state["last_deploy_request_sha"] = latest_commit
        self.state["last_deploy_request_at"] = datetime.now(UTC).isoformat()
        self.save_state()
        self.last_check_conclusive = True
        self.log(
            "Requested CapRover deployment for "
            f"{latest_commit[:12]} (currently {deployed_commit[:12]})"
        )
        return True
