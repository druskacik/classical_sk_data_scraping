from __future__ import annotations

import html
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Notification:
    title: str
    message: str
    severity: str = "warning"


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    chat_id: str
    timeout_seconds: int = 10

    @classmethod
    def from_environment(cls) -> TelegramConfig | None:
        bot_token = os.getenv("TELEGRAM_ALERT_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_ALERT_CHAT_ID", "").strip()
        if not bot_token or not chat_id:
            return None
        return cls(bot_token=bot_token, chat_id=chat_id)


def send_notification(notification: Notification) -> bool:
    """Deliver a notification when a configured provider is available.

    Notification delivery is deliberately best-effort: callers must continue
    their safety or recovery path even when Telegram is unavailable.
    """
    config = TelegramConfig.from_environment()
    if config is None:
        logger.warning(
            "Notification was not delivered because Telegram alerts are not configured",
            extra={
                "event": "notification_not_configured",
                "channel": "telegram",
                "notification_title": notification.title,
            },
        )
        return False

    body = urllib.parse.urlencode(
        {
            "chat_id": config.chat_id,
            "parse_mode": "HTML",
            "text": (
                f"<b>{html.escape(notification.title)}</b>\n"
                f"{html.escape(notification.message)}"
            ),
        }
    ).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{config.bot_token}/sendMessage",
        data=body,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
            response.read()
    except (OSError, urllib.error.URLError) as error:
        logger.error(
            "Telegram notification delivery failed",
            extra={
                "event": "notification_delivery_failed",
                "channel": "telegram",
                "notification_title": notification.title,
                "error_type": type(error).__name__,
                "http_status": getattr(error, "code", None),
            },
        )
        return False

    logger.info(
        "Telegram notification delivered",
        extra={
            "event": "notification_delivered",
            "channel": "telegram",
            "notification_title": notification.title,
            "severity": notification.severity,
        },
    )
    return True
