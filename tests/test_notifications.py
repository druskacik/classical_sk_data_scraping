import os
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from automation.notifications import Notification, send_notification


class NotificationTests(unittest.TestCase):
    def test_unconfigured_notification_is_skipped(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(send_notification(Notification("Title", "Message")))

    def test_telegram_notification_escapes_html(self):
        response = MagicMock()
        response.__enter__.return_value = response
        with (
            patch.dict(
                os.environ,
                {
                    "TELEGRAM_ALERT_BOT_TOKEN": "secret-token",
                    "TELEGRAM_ALERT_CHAT_ID": "12345",
                },
                clear=True,
            ),
            patch("automation.notifications.urllib.request.urlopen", return_value=response) as send,
        ):
            delivered = send_notification(Notification("Auth <required>", "A & B"))

        self.assertTrue(delivered)
        request = send.call_args.args[0]
        self.assertNotIn(b"<required>", request.data)
        self.assertIn(b"Auth+%26lt%3Brequired%26gt%3B", request.data)
        self.assertIn(b"A+%26amp%3B+B", request.data)

    def test_delivery_failure_is_best_effort(self):
        with (
            patch.dict(
                os.environ,
                {
                    "TELEGRAM_ALERT_BOT_TOKEN": "secret-token",
                    "TELEGRAM_ALERT_CHAT_ID": "12345",
                },
                clear=True,
            ),
            patch(
                "automation.notifications.urllib.request.urlopen",
                side_effect=urllib.error.URLError("offline"),
            ),
        ):
            self.assertFalse(send_notification(Notification("Title", "Message")))
