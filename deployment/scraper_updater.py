from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from pathlib import Path

from deployment.caprover_updater import CapRoverUpdater, CapRoverUpdaterConfig


DEFAULT_REPOSITORY = "https://github.com/druskacik/classical_bot.git"
DEFAULT_STATE_PATH = Path("/var/lib/classical-bot/deployment-state.json")
logger = logging.getLogger(__name__)


def log(message: str) -> None:
    event = "scraper_update_status"
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
    elif message.startswith("Daily pipeline started"):
        event = "daily_pipeline_started"
    elif message.startswith("Daily pipeline finished"):
        event = "daily_pipeline_finished"
    level = logging.WARNING if event.endswith(("_failed", "_invalid", "_disabled")) else logging.INFO
    logger.log(level, message, extra={"event": event})


@dataclass(frozen=True)
class UpdaterConfig:
    repository: str
    deploy_webhook: str | None
    state_path: Path

    @classmethod
    def from_environment(cls) -> UpdaterConfig:
        return cls(
            repository=os.getenv("SCRAPER_REPOSITORY", DEFAULT_REPOSITORY),
            deploy_webhook=os.getenv("SCRAPER_DEPLOY_WEBHOOK") or None,
            state_path=Path(os.getenv("SCRAPER_DEPLOY_STATE_PATH", str(DEFAULT_STATE_PATH))),
        )


class ScraperUpdater:
    def __init__(self, config: UpdaterConfig) -> None:
        self.updater = CapRoverUpdater(
            CapRoverUpdaterConfig(
                repository=config.repository,
                deploy_webhook=config.deploy_webhook,
                webhook_environment_name="SCRAPER_DEPLOY_WEBHOOK",
                state_path=config.state_path,
            ),
            log,
        )
        self.pipeline_active = False
        self.updates_enabled = False

    def begin_daily_pipeline(self) -> None:
        self.pipeline_active = True
        self.updates_enabled = False
        log("Daily pipeline started; automatic deployment is paused")

    def finish_daily_pipeline(self) -> None:
        self.pipeline_active = False
        self.updates_enabled = True
        log("Daily pipeline finished; checking master for an update")
        self.check_for_update()

    def check_for_update(self) -> bool:
        if self.pipeline_active or not self.updates_enabled:
            return False
        checked = self.updater.check_for_update()
        if self.updater.last_check_conclusive:
            self.updates_enabled = False
        return checked
