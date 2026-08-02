from __future__ import annotations

import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

from pythonjsonlogger.json import JsonFormatter


LOG_SCHEMA = "classical_bot.log.v1"
DEFAULT_SERVICE = "classical-bot"
_CONFIGURED_HANDLER = "classical_bot_json"


class ClassicalBotJsonFormatter(JsonFormatter):
    """Render application LogRecords as the stable VictoriaLogs JSON schema."""

    def add_fields(
        self,
        log_data: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_data, record, message_dict)
        log_data["schema"] = LOG_SCHEMA
        log_data["timestamp"] = datetime.fromtimestamp(record.created, UTC).isoformat()
        log_data["level"] = record.levelname.lower()
        log_data["logger"] = record.name
        log_data.setdefault("event", "log_message")


def configure_logging(service: str | None = None) -> None:
    """Configure one JSON stdout handler for this process."""

    root = logging.getLogger()
    level_name = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    root.setLevel(level)

    for handler in root.handlers:
        if getattr(handler, "name", None) == _CONFIGURED_HANDLER:
            handler.setLevel(level)
            return

    handler = logging.StreamHandler(sys.stdout)
    handler.name = _CONFIGURED_HANDLER
    handler.setLevel(level)
    formatter = ClassicalBotJsonFormatter(
        "{message}",
        style="{",
        defaults={"service": service or os.getenv("LOG_SERVICE", DEFAULT_SERVICE)},
        rename_fields={"exc_info": "exception"},
    )
    handler.setFormatter(formatter)
    root.handlers.clear()
    root.addHandler(handler)


def log_message(
    message: object,
    *,
    event: str = "crawler_message",
    level: int = logging.DEBUG,
    **fields: Any,
) -> None:
    """Emit a bounded structured event from a crawler-specific code path."""

    logging.getLogger("crawlers").log(
        level,
        str(message)[:2000],
        extra={"event": event, **fields},
    )
