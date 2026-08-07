from __future__ import annotations

import json
import os
import logging
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, time as wall_time, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from observability import configure_logging

from deployment.caprover_updater import (
    CapRoverUpdater,
    CapRoverUpdaterConfig,
    load_state,
    normalize_commit_sha,
    remote_commit,
    save_state,
)


DEFAULT_SERVICE_STATE_PATH = Path("/var/lib/crawler-factory/service-state.json")
DEFAULT_REPOSITORY = "https://github.com/druskacik/classical_bot.git"
CONTINUOUS_MODE = "continuous"
SCHEDULED_MODE = "scheduled"
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
    elif message.startswith("Starting crawler-factory batch"):
        event = "factory_batch_started"
    elif message.startswith("Crawler-factory batch finished"):
        event = "factory_batch_completed"
    elif message.startswith("Waiting for crawler-factory pull request"):
        event = "factory_pr_waiting"
    elif message.startswith("Crawler-factory pull request merged"):
        event = "factory_pr_merged"
    elif message.startswith("Crawler-factory pull request closed without merging"):
        event = "factory_pr_closed_unmerged"
    elif message.startswith("Updating crawler-factory pull request"):
        event = "factory_pr_update_started"
    elif message.startswith("Updated crawler-factory pull request"):
        event = "factory_pr_update_succeeded"
    elif message.startswith("Could not update crawler-factory pull request"):
        event = "factory_pr_update_failed"
    elif message.startswith("Crawler-factory pull request update has merge conflicts"):
        event = "factory_pr_update_conflict"
    elif message.startswith("Could not check crawler-factory pull request"):
        event = "factory_pr_check_failed"
    elif message.startswith("Crawler-only master update"):
        event = "factory_crawler_only_update"
    elif message.startswith("Factory-relevant master update"):
        event = "factory_deployment_pending"
    elif message.startswith("Could not classify master update"):
        event = "factory_update_classification_failed"
    elif message.startswith("Received signal"):
        event = "factory_service_signal_received"
    elif message == "Stopped":
        event = "factory_service_stopped"
    level = logging.WARNING if (
        event.endswith(("_failed", "_invalid", "_disabled", "_closed_unmerged"))
        or event == "factory_pr_update_conflict"
    ) else logging.INFO
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


def parse_mode(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {CONTINUOUS_MODE, SCHEDULED_MODE}:
        raise ValueError("CRAWLER_FACTORY_MODE must be 'continuous' or 'scheduled'")
    return normalized


def changed_paths_between(repository: str, old_commit: str, new_commit: str) -> list[str]:
    local_repository = Path(repository)
    git_repository = str(local_repository.resolve()) if local_repository.exists() else repository
    with tempfile.TemporaryDirectory(prefix="crawler-factory-update-") as temporary:
        checkout = Path(temporary)
        commands = [
            ["git", "init", "--quiet", str(checkout)],
            ["git", "-C", str(checkout), "remote", "add", "origin", git_repository],
            [
                "git", "-C", str(checkout), "fetch", "--quiet", "--no-tags", "--depth=1",
                "origin", old_commit,
            ],
            [
                "git", "-C", str(checkout), "fetch", "--quiet", "--no-tags", "--depth=1",
                "origin", new_commit,
            ],
        ]
        for command in commands:
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
        result = subprocess.run(
            [
                "git", "-C", str(checkout), "diff", "--name-only", "--no-renames",
                old_commit, new_commit,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    return [line for line in result.stdout.splitlines() if line]


def pull_request_belongs_to_repository(pull_request_url: str, repository: str) -> bool:
    repository_value = repository.removesuffix(".git")
    if repository_value.startswith("git@") and ":" in repository_value:
        host_path = repository_value.removeprefix("git@").replace(":", "/", 1)
        repository_value = f"https://{host_path}"
    parsed_repository = urlparse(repository_value)
    parsed_pull_request = urlparse(pull_request_url)
    if not parsed_repository.netloc or not parsed_repository.path:
        return False
    return (
        parsed_pull_request.scheme == parsed_repository.scheme
        and parsed_pull_request.netloc == parsed_repository.netloc
        and parsed_pull_request.path.startswith(
            f"{parsed_repository.path.rstrip('/')}/pull/"
        )
    )


@dataclass(frozen=True)
class BatchOutcome:
    return_code: int
    claimed_count: int | None
    status: str | None
    pull_request_url: str | None = None
    base_commit_sha: str | None = None


@dataclass(frozen=True)
class ServiceConfig:
    repository: str
    mode: str
    schedule_time: wall_time
    timezone: ZoneInfo
    update_interval_seconds: int
    idle_interval_seconds: int
    failure_backoff_seconds: int
    pr_poll_interval_seconds: int
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
            mode=parse_mode(os.getenv("CRAWLER_FACTORY_MODE", CONTINUOUS_MODE)),
            schedule_time=parse_schedule(os.getenv("CRAWLER_FACTORY_SCHEDULE_TIME", "06:00")),
            timezone=timezone,
            update_interval_seconds=update_minutes * 60,
            idle_interval_seconds=positive_integer(
                os.getenv("CRAWLER_FACTORY_IDLE_INTERVAL_MINUTES", "5"),
                "CRAWLER_FACTORY_IDLE_INTERVAL_MINUTES",
            ) * 60,
            failure_backoff_seconds=positive_integer(
                os.getenv("CRAWLER_FACTORY_FAILURE_BACKOFF_MINUTES", "15"),
                "CRAWLER_FACTORY_FAILURE_BACKOFF_MINUTES",
            ) * 60,
            pr_poll_interval_seconds=positive_integer(
                os.getenv("CRAWLER_FACTORY_PR_POLL_INTERVAL_MINUTES", "1"),
                "CRAWLER_FACTORY_PR_POLL_INTERVAL_MINUTES",
            ) * 60,
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
        self.deployment_pending = False
        self.update_check_conclusive = True
        self._last_pending_pr_observation: tuple[str, ...] | None = None

    def stop(self, signum: int, _frame: object = None) -> None:
        log(f"Received signal {signum}; stopping")
        self.stop_event.set()
        if self.child is not None and self.child.poll() is None:
            self.child.send_signal(signum)

    def run_factory(self, now: datetime) -> BatchOutcome:
        if self.config.mode == SCHEDULED_MODE:
            self.state["last_factory_attempt_date"] = now.date().isoformat()
            self.updater.save_state()
        result_path = self.config.state_path.parent / "last-batch-result.json"
        result_path.unlink(missing_ok=True)
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
            "--result-path",
            str(result_path),
        ]
        log(
            f"Starting crawler-factory batch for {now.date().isoformat()} "
            f"(max URLs: {self.config.max_urls})"
        )
        self.child = subprocess.Popen(command)
        try:
            while self.child.poll() is None:
                if self.stop_event.wait(1):
                    try:
                        return BatchOutcome(self.child.wait(timeout=30), None, None)
                    except subprocess.TimeoutExpired:
                        log("Factory child did not stop within 30 seconds; killing it")
                        self.child.kill()
                        return BatchOutcome(self.child.wait(), None, None)
            try:
                payload = load_state(result_path, log)
                claimed_count = int(payload["claimed_count"])
                status = str(payload["status"])
                raw_pr_url = payload.get("pull_request_url")
                pull_request_url = str(raw_pr_url) if raw_pr_url else None
                raw_base_sha = payload.get("base_commit_sha")
                base_commit_sha = (
                    normalize_commit_sha(str(raw_base_sha), "base_commit_sha")
                    if raw_base_sha
                    else None
                )
            except (KeyError, TypeError, ValueError):
                claimed_count = None
                status = None
                pull_request_url = None
                base_commit_sha = None
            return BatchOutcome(
                self.child.returncode,
                claimed_count,
                status,
                pull_request_url,
                base_commit_sha,
            )
        finally:
            return_code = self.child.returncode
            self.child = None
            log(f"Crawler-factory batch finished with status {return_code}")

    def check_for_update(self) -> bool:
        if self.child is not None and self.child.poll() is None:
            return False
        self.deployment_pending = False
        self.update_check_conclusive = False
        deployed_value = os.getenv("CAPROVER_GIT_COMMIT_SHA")
        if not self.config.deploy_webhook or not deployed_value:
            requested = self.updater.check_for_update()
            self.update_check_conclusive = self.updater.last_check_conclusive
            return requested
        try:
            deployed_commit = normalize_commit_sha(deployed_value, "CAPROVER_GIT_COMMIT_SHA")
            latest_commit = remote_commit(self.config.repository)
            baseline_value = self.state.get("last_factory_checked_sha", deployed_commit)
            baseline_commit = normalize_commit_sha(baseline_value, "last_factory_checked_sha")
            if latest_commit == deployed_commit:
                self.state["last_factory_checked_sha"] = latest_commit
                self.updater.save_state()
                self.update_check_conclusive = True
                return False
            if latest_commit == baseline_commit:
                self.update_check_conclusive = True
                return False
            changed_paths = changed_paths_between(
                self.config.repository,
                baseline_commit,
                latest_commit,
            )
        except Exception as exc:
            log(f"Could not classify master update: {type(exc).__name__}: {exc}")
            return False
        if changed_paths and all(path.startswith("crawlers/") for path in changed_paths):
            self.state["last_factory_checked_sha"] = latest_commit
            self.updater.save_state()
            self.update_check_conclusive = True
            log(
                f"Crawler-only master update through {latest_commit[:12]}; "
                "continuing without deployment"
            )
            return False
        self.deployment_pending = True
        log(
            f"Factory-relevant master update through {latest_commit[:12]}; "
            "draining until deployment"
        )
        requested = self.updater.check_for_update(latest_commit=latest_commit)
        self.update_check_conclusive = self.updater.last_check_conclusive
        return requested

    def wait(self, seconds: int) -> None:
        self.stop_event.wait(seconds)

    def remember_pending_pull_request(
        self,
        pull_request_url: str,
        base_commit_sha: str | None = None,
    ) -> None:
        self.state["pending_factory_pr_url"] = pull_request_url
        if base_commit_sha:
            self.state["pending_factory_pr_base_sha"] = normalize_commit_sha(
                base_commit_sha,
                "pending pull request base SHA",
            )
        else:
            self.state.pop("pending_factory_pr_base_sha", None)
        self._clear_pending_pr_update_retry()
        self.updater.save_state()
        self._last_pending_pr_observation = None

    def clear_pending_pull_request(self) -> None:
        for key in (
            "pending_factory_pr_url",
            "pending_factory_pr_base_sha",
            "pending_factory_pr_update_base_sha",
            "pending_factory_pr_update_attempts",
            "pending_factory_pr_update_retry_at",
        ):
            self.state.pop(key, None)
        self.updater.save_state()
        self._last_pending_pr_observation = None

    def _clear_pending_pr_update_retry(self) -> None:
        self.state.pop("pending_factory_pr_update_base_sha", None)
        self.state.pop("pending_factory_pr_update_attempts", None)
        self.state.pop("pending_factory_pr_update_retry_at", None)

    def _pending_pr_update_is_due(self, base_sha: str, now: datetime) -> bool:
        if self.state.get("pending_factory_pr_update_base_sha") != base_sha:
            return True
        try:
            retry_at = datetime.fromisoformat(
                str(self.state["pending_factory_pr_update_retry_at"])
            )
        except (KeyError, TypeError, ValueError):
            return True
        return retry_at.tzinfo is None or now >= retry_at

    def _record_pending_pr_update_failure(
        self,
        base_sha: str,
        now: datetime,
    ) -> int:
        if self.state.get("pending_factory_pr_update_base_sha") == base_sha:
            attempts = int(self.state.get("pending_factory_pr_update_attempts", 0)) + 1
        else:
            attempts = 1
        delay = min(
            self.config.pr_poll_interval_seconds * (2 ** min(attempts - 1, 10)),
            self.config.failure_backoff_seconds,
        )
        self.state["pending_factory_pr_update_base_sha"] = base_sha
        self.state["pending_factory_pr_update_attempts"] = attempts
        self.state["pending_factory_pr_update_retry_at"] = (
            now + timedelta(seconds=delay)
        ).isoformat()
        self.updater.save_state()
        return delay

    def _update_pending_pull_request(
        self,
        pull_request_url: str,
        base_sha: str,
    ) -> None:
        now = datetime.now(UTC)
        if not self._pending_pr_update_is_due(base_sha, now):
            return
        log(
            f"Updating crawler-factory pull request {pull_request_url} "
            f"to master {base_sha[:12]}"
        )
        try:
            subprocess.run(
                ["gh", "pr", "update-branch", pull_request_url],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except Exception as exc:
            delay = self._record_pending_pr_update_failure(base_sha, now)
            stderr = getattr(exc, "stderr", None)
            detail = str(stderr or exc).strip()
            if "conflict" in detail.lower():
                log(
                    "Crawler-factory pull request update has merge conflicts: "
                    f"{pull_request_url}; generated work is preserved; "
                    f"retrying in {delay // 60} minutes"
                )
            else:
                log(
                    f"Could not update crawler-factory pull request {pull_request_url}: "
                    f"{type(exc).__name__}: {detail}; "
                    f"retrying in {delay // 60} minutes"
                )
            return
        self.state["pending_factory_pr_base_sha"] = base_sha
        self._clear_pending_pr_update_retry()
        self.updater.save_state()
        self._last_pending_pr_observation = None
        log(
            f"Updated crawler-factory pull request {pull_request_url} "
            f"to master {base_sha[:12]}"
        )

    def pending_pull_request_is_open(self) -> bool:
        pull_request_url = self.state.get("pending_factory_pr_url")
        if not pull_request_url:
            self._last_pending_pr_observation = None
            return False
        try:
            if not pull_request_belongs_to_repository(
                str(pull_request_url),
                self.config.repository,
            ):
                raise ValueError("pending pull request belongs to an unexpected repository")
            result = subprocess.run(
                [
                    "gh",
                    "pr",
                    "view",
                    str(pull_request_url),
                    "--json",
                    "state,mergedAt,mergeStateStatus,statusCheckRollup,"
                    "baseRefName,baseRefOid,headRefName,headRefOid,isCrossRepository",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            payload = json.loads(result.stdout)
            if not isinstance(payload, dict):
                raise ValueError("GitHub response is not an object")
            state = str(payload.get("state") or "").upper()
            merged_at = payload.get("mergedAt")
            if merged_at:
                log(f"Crawler-factory pull request merged: {pull_request_url}")
                self.clear_pending_pull_request()
                return False
            if state == "CLOSED":
                log(
                    "Crawler-factory pull request closed without merging: "
                    f"{pull_request_url}"
                )
                self.clear_pending_pull_request()
                return False
            if state != "OPEN":
                raise ValueError(f"unexpected pull request state: {state or 'missing'}")
            base_ref = str(payload.get("baseRefName") or "")
            head_ref = str(payload.get("headRefName") or "")
            if (
                base_ref != "master"
                or not head_ref.startswith("crawler-factory/")
                or payload.get("isCrossRepository") is not False
            ):
                raise ValueError(
                    "refusing to update unexpected pull request "
                    f"(base={base_ref or 'missing'}, head={head_ref or 'missing'}, "
                    f"cross-repository={payload.get('isCrossRepository')!r})"
                )
            base_sha = normalize_commit_sha(
                str(payload.get("baseRefOid") or ""),
                "pull request base SHA",
            )
            normalize_commit_sha(
                str(payload.get("headRefOid") or ""),
                "pull request head SHA",
            )
            known_base_sha = self.state.get("pending_factory_pr_base_sha")
            if known_base_sha is None:
                merge_state = str(
                    payload.get("mergeStateStatus") or "UNKNOWN"
                ).upper()
                if merge_state == "BEHIND":
                    self._update_pending_pull_request(str(pull_request_url), base_sha)
                else:
                    self.state["pending_factory_pr_base_sha"] = base_sha
                    self.updater.save_state()
            elif normalize_commit_sha(
                str(known_base_sha),
                "pending pull request base SHA",
            ) != base_sha:
                self._update_pending_pull_request(str(pull_request_url), base_sha)
            checks = payload.get("statusCheckRollup") or []
            conclusions = sorted(
                str(check.get("conclusion") or check.get("state") or "PENDING").upper()
                for check in checks
                if isinstance(check, dict)
            )
            observation = (
                "open",
                str(payload.get("mergeStateStatus") or "UNKNOWN").upper(),
                *conclusions,
            )
            if observation != self._last_pending_pr_observation:
                check_summary = ", ".join(conclusions) if conclusions else "no checks"
                log(
                    f"Waiting for crawler-factory pull request {pull_request_url} "
                    f"(merge state: {observation[1]}; checks: {check_summary})"
                )
                self._last_pending_pr_observation = observation
            return True
        except Exception as exc:
            observation = ("error", type(exc).__name__, str(exc))
            if observation != self._last_pending_pr_observation:
                log(
                    f"Could not check crawler-factory pull request {pull_request_url}: "
                    f"{type(exc).__name__}: {exc}"
                )
                self._last_pending_pr_observation = observation
            return True

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        prepare_git_authentication()
        next_update_check = 0.0
        if self.config.mode == CONTINUOUS_MODE:
            log(
                f"Running continuous batches of at most {self.config.max_urls}; "
                f"idle polling every {self.config.idle_interval_seconds // 60} minutes"
            )
        else:
            log(
                f"Scheduling one daily batch at {self.config.schedule_time:%H:%M} "
                f"{self.config.timezone.key}; checking for updates every "
                f"{self.config.update_interval_seconds // 60} minutes"
            )
        while not self.stop_event.is_set():
            if self.pending_pull_request_is_open():
                self.wait(self.config.pr_poll_interval_seconds)
                continue
            now = datetime.now(self.config.timezone)
            should_run = self.config.mode == CONTINUOUS_MODE or batch_is_due(
                now, self.config.schedule_time, self.state
            )
            if should_run:
                self.check_for_update()
                if self.deployment_pending or not self.update_check_conclusive:
                    self.wait(self.config.update_interval_seconds)
                    continue
                outcome = self.run_factory(now)
                if self.stop_event.is_set():
                    break
                if outcome.pull_request_url:
                    self.remember_pending_pull_request(
                        outcome.pull_request_url,
                        outcome.base_commit_sha,
                    )
                    continue
                if self.config.mode == SCHEDULED_MODE:
                    self.check_for_update()
                    next_update_check = time.monotonic() + self.config.update_interval_seconds
                    continue
                if outcome.return_code != 0:
                    self.wait(self.config.failure_backoff_seconds)
                elif outcome.claimed_count != self.config.max_urls:
                    self.wait(self.config.idle_interval_seconds)
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
