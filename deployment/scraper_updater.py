from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from deployment.caprover_updater import CapRoverUpdater, CapRoverUpdaterConfig


DEFAULT_REPOSITORY = "https://github.com/druskacik/classical_bot.git"
DEFAULT_STATE_PATH = Path("/var/lib/classical-bot/deployment-state.json")


def log(message: str) -> None:
    print(f"[scraper-updater] {message}", flush=True)


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
