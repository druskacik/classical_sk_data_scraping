from __future__ import annotations

import os
import logging
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, time as wall_time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from observability import configure_logging

from deployment.caprover_updater import (
    CapRoverUpdater,
    CapRoverUpdaterConfig,
    load_state,
    save_state,
)


DEFAULT_SERVICE_STATE_PATH = Path("/var/lib/crawler-factory/service-state.json")
DEFAULT_REPOSITORY = "https://github.com/druskacik/classical_bot.git"
logger = logging.getLogger(__name__)


def log(message: str) -> None:
    event = "factory_service_status"
    if message.startswith("Requested CapRover deployment"):
        event = "deployment_requested"
    elif message.startswith("Could not request CapRover deployment"):
        event = "deployment_request_failed"
    elif message.startswith("Could not check master"):
        event = "deployment_check_failed"
    elif message.startswith("Automatic updates disabled"):
        event = "deployment_updates_disabled"
    elif message.startswith("Ignoring unreadable deployment state"):
        event = "deployment_state_invalid"
    elif message.startswith("Starting daily crawler-factory batch"):
        event = "factory_batch_started"
    elif message.startswith("Daily batch finished"):
        event = "factory_batch_completed"
    elif message.startswith("Received signal"):
        event = "factory_service_signal_received"
    elif message == "Stopped":
        event = "factory_service_stopped"
    level = logging.WARNING if event.endswith(("_failed", "_invalid", "_disabled")) else logging.INFO
    logger.log(level, message, extra={"event": event})


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
    validation_timeout_minutes: int
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
            validation_timeout_minutes=positive_integer(
                os.getenv("CRAWLER_FACTORY_VALIDATION_TIMEOUT_MINUTES", "15"),
                "CRAWLER_FACTORY_VALIDATION_TIMEOUT_MINUTES",
            ),
            state_path=Path(
                os.getenv("CRAWLER_FACTORY_SERVICE_STATE_PATH", str(DEFAULT_SERVICE_STATE_PATH))
            ),
        )


def load_service_state(path: Path) -> dict:
    return load_state(path, log)


def save_service_state(path: Path, state: dict) -> None:
    save_state(path, state)


def batch_is_due(now: datetime, schedule_time: wall_time, state: dict) -> bool:
    return (
        now.time() >= schedule_time
        and state.get("last_factory_attempt_date") != now.date().isoformat()
    )


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
        self.updater = CapRoverUpdater(
            CapRoverUpdaterConfig(
                repository=config.repository,
                deploy_webhook=config.deploy_webhook,
                webhook_environment_name="CRAWLER_FACTORY_DEPLOY_WEBHOOK",
                state_path=config.state_path,
            ),
            log,
        )
        self.state = self.updater.state
        self.stop_event = threading.Event()
        self.child: subprocess.Popen | None = None

    def stop(self, signum: int, _frame: object = None) -> None:
        log(f"Received signal {signum}; stopping")
        self.stop_event.set()
        if self.child is not None and self.child.poll() is None:
            self.child.send_signal(signum)

    def run_factory(self, now: datetime) -> int:
        self.state["last_factory_attempt_date"] = now.date().isoformat()
        self.updater.save_state()
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
            "--validation-timeout-minutes",
            str(self.config.validation_timeout_minutes),
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

    def check_for_update(self) -> bool:
        if self.child is not None and self.child.poll() is None:
            return False
        return self.updater.check_for_update()

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
                self.check_for_update()
                next_update_check = monotonic_now + self.config.update_interval_seconds
            self.stop_event.wait(1)
        log("Stopped")


def main() -> None:
    configure_logging("classical-crawler-factory")
    try:
        config = ServiceConfig.from_environment()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    FactoryService(config).run()


if __name__ == "__main__":
    main()
