from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, time as wall_time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_SERVICE_STATE_PATH = Path("/var/lib/crawler-factory/service-state.json")
DEFAULT_REPOSITORY = "https://github.com/druskacik/classical_sk_data_scraping.git"
DEPLOY_RETRY_SECONDS = 30 * 60


def log(message: str) -> None:
    print(f"[crawler-factory-service] {message}", flush=True)


def positive_integer(value: str, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed < 1:
        raise ValueError(f"{name} must be at least 1")
    return parsed


def parse_schedule(value: str) -> wall_time:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise ValueError("CRAWLER_FACTORY_SCHEDULE_TIME must use HH:MM in 24-hour time") from exc


@dataclass(frozen=True)
class ServiceConfig:
    repository: str
    schedule_time: wall_time
    timezone: ZoneInfo
    update_interval_seconds: int
    deploy_webhook: str | None
    max_urls: int
    timeout_minutes: int
    state_path: Path

    @classmethod
    def from_environment(cls) -> ServiceConfig:
        timezone_name = os.getenv("CRAWLER_FACTORY_TIMEZONE", "Europe/Prague")
        try:
            timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"CRAWLER_FACTORY_TIMEZONE is not a known timezone: {timezone_name}"
            ) from exc
        update_minutes = positive_integer(
            os.getenv("CRAWLER_FACTORY_UPDATE_INTERVAL_MINUTES", "5"),
            "CRAWLER_FACTORY_UPDATE_INTERVAL_MINUTES",
        )
        return cls(
            repository=os.getenv("CRAWLER_FACTORY_REPOSITORY", DEFAULT_REPOSITORY),
            schedule_time=parse_schedule(os.getenv("CRAWLER_FACTORY_SCHEDULE_TIME", "06:00")),
            timezone=timezone,
            update_interval_seconds=update_minutes * 60,
            deploy_webhook=os.getenv("CRAWLER_FACTORY_DEPLOY_WEBHOOK") or None,
            max_urls=positive_integer(
                os.getenv("CRAWLER_FACTORY_MAX_URLS", "5"),
                "CRAWLER_FACTORY_MAX_URLS",
            ),
            timeout_minutes=positive_integer(
                os.getenv("CRAWLER_FACTORY_TIMEOUT_MINUTES", "60"),
                "CRAWLER_FACTORY_TIMEOUT_MINUTES",
            ),
            state_path=Path(
                os.getenv("CRAWLER_FACTORY_SERVICE_STATE_PATH", str(DEFAULT_SERVICE_STATE_PATH))
            ),
        )


def load_service_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log(f"Ignoring unreadable service state at {path}")
        return {}
    return state if isinstance(state, dict) else {}


def save_service_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def batch_is_due(now: datetime, schedule_time: wall_time, state: dict) -> bool:
    return (
        now.time() >= schedule_time
        and state.get("last_factory_attempt_date") != now.date().isoformat()
    )


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
    return fields[0]


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


def prepare_git_authentication() -> None:
    if not os.getenv("GH_TOKEN"):
        return
    try:
        subprocess.run(
            ["gh", "auth", "setup-git"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as exc:
        log(f"Could not configure GitHub authentication: {type(exc).__name__}: {exc}")


class FactoryService:
    def __init__(self, config: ServiceConfig) -> None:
        self.config = config
        self.state = load_service_state(config.state_path)
        self.stop_event = threading.Event()
        self.child: subprocess.Popen | None = None
        self.last_deploy_request_sha: str | None = None
        self.last_deploy_request_at = 0.0
        self._configuration_warning_logged = False

    def stop(self, signum: int, _frame: object = None) -> None:
        log(f"Received signal {signum}; stopping")
        self.stop_event.set()
        if self.child is not None and self.child.poll() is None:
            self.child.send_signal(signum)

    def run_factory(self, now: datetime) -> int:
        self.state["last_factory_attempt_date"] = now.date().isoformat()
        save_service_state(self.config.state_path, self.state)
        command = [
            sys.executable,
            "-m",
            "automation.run_crawler_factory",
            "--repository",
            self.config.repository,
            "--max-urls",
            str(self.config.max_urls),
            "--timeout-minutes",
            str(self.config.timeout_minutes),
        ]
        log(
            f"Starting daily batch for {now.date().isoformat()} "
            f"(max URLs: {self.config.max_urls})"
        )
        self.child = subprocess.Popen(command)
        try:
            while self.child.poll() is None:
                if self.stop_event.wait(1):
                    try:
                        return self.child.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        log("Factory child did not stop within 30 seconds; killing it")
                        self.child.kill()
                        return self.child.wait()
            return self.child.returncode
        finally:
            return_code = self.child.returncode
            self.child = None
            log(f"Daily batch finished with status {return_code}")

    def check_for_update(self, monotonic_now: float | None = None) -> bool:
        if self.child is not None and self.child.poll() is None:
            return False
        deployed_commit = os.getenv("CAPROVER_GIT_COMMIT_SHA")
        if not self.config.deploy_webhook or not deployed_commit:
            if not self._configuration_warning_logged:
                missing = []
                if not self.config.deploy_webhook:
                    missing.append("CRAWLER_FACTORY_DEPLOY_WEBHOOK")
                if not deployed_commit:
                    missing.append("CAPROVER_GIT_COMMIT_SHA")
                log(f"Automatic updates disabled; missing {', '.join(missing)}")
                self._configuration_warning_logged = True
            return False
        try:
            latest_commit = remote_commit(self.config.repository)
        except Exception as exc:
            log(f"Could not check master for updates: {type(exc).__name__}: {exc}")
            return False
        if latest_commit == deployed_commit:
            return False
        checked_at = time.monotonic() if monotonic_now is None else monotonic_now
        if (
            latest_commit == self.last_deploy_request_sha
            and checked_at - self.last_deploy_request_at < DEPLOY_RETRY_SECONDS
        ):
            return False
        try:
            request_deployment(self.config.deploy_webhook)
        except Exception as exc:
            log(f"Could not request CapRover deployment: {type(exc).__name__}: {exc}")
            return False
        self.last_deploy_request_sha = latest_commit
        self.last_deploy_request_at = checked_at
        log(
            "Requested CapRover deployment for "
            f"{latest_commit[:12]} (currently {deployed_commit[:12]})"
        )
        return True

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        prepare_git_authentication()
        next_update_check = 0.0
        log(
            f"Scheduling one daily batch at {self.config.schedule_time:%H:%M} "
            f"{self.config.timezone.key}; checking for updates every "
            f"{self.config.update_interval_seconds // 60} minutes"
        )
        while not self.stop_event.is_set():
            now = datetime.now(self.config.timezone)
            if batch_is_due(now, self.config.schedule_time, self.state):
                self.run_factory(now)
                if self.stop_event.is_set():
                    break
                self.check_for_update()
                next_update_check = time.monotonic() + self.config.update_interval_seconds
                continue
            monotonic_now = time.monotonic()
            if monotonic_now >= next_update_check:
                self.check_for_update(monotonic_now)
                next_update_check = monotonic_now + self.config.update_interval_seconds
            self.stop_event.wait(1)
        log("Stopped")


def main() -> None:
    try:
        config = ServiceConfig.from_environment()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    FactoryService(config).run()


if __name__ == "__main__":
    main()
