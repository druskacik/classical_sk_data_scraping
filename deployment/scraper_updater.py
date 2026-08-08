from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


DEFAULT_REQUEST_PATH = Path("/var/lib/classical-bot/update-check-request.json")
logger = logging.getLogger(__name__)


def log(message: str, *, error: bool = False) -> None:
    logger.log(
        logging.ERROR if error else logging.INFO,
        message,
        extra={
            "event": "daily_update_check_request_failed"
            if error
            else "daily_update_check_requested"
        },
    )


@dataclass(frozen=True)
class UpdaterConfig:
    request_path: Path

    @classmethod
    def from_environment(cls) -> UpdaterConfig:
        return cls(
            request_path=Path(
                os.getenv("SCRAPER_UPDATE_REQUEST_PATH", str(DEFAULT_REQUEST_PATH))
            )
        )


class ScraperUpdater:
    """Notify the parent service that the daily pipeline permits an update check."""

    def __init__(self, config: UpdaterConfig) -> None:
        self.config = config
        self.pipeline_active = False
        self.request_pending = False

    def begin_daily_pipeline(self) -> None:
        self.pipeline_active = True
        self.request_pending = False
        logger.info(
            "Daily pipeline started; automatic deployment is paused",
            extra={"event": "daily_pipeline_started"},
        )

    def finish_daily_pipeline(self) -> None:
        self.pipeline_active = False
        self.request_pending = True
        logger.info(
            "Daily pipeline finished; requesting an update check",
            extra={"event": "daily_pipeline_finished"},
        )
        self.request_update_check()

    def request_update_check(self) -> bool:
        if self.pipeline_active or not self.request_pending:
            return False
        try:
            self.config.request_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.config.request_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"requested_at": datetime.now(UTC).isoformat()}) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.config.request_path)
        except OSError as error:
            log(f"Could not request an update check: {type(error).__name__}: {error}", error=True)
            return False
        self.request_pending = False
        log("Requested an update check after the daily pipeline")
        return True
