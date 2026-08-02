import io
import json
import logging
import os
import unittest
from unittest.mock import patch

from observability.logging import LOG_SCHEMA, configure_logging, log_message


class JsonLoggingTests(unittest.TestCase):
    def setUp(self):
        self.root = logging.getLogger()
        self.original_handlers = self.root.handlers[:]
        self.original_level = self.root.level
        self.root.handlers.clear()

    def tearDown(self):
        self.root.handlers.clear()
        self.root.handlers.extend(self.original_handlers)
        self.root.setLevel(self.original_level)

    def configure_with_buffer(self, service="test-service"):
        buffer = io.StringIO()
        with patch("sys.stdout", buffer):
            configure_logging(service)
        return buffer

    def test_emits_one_structured_json_line(self):
        buffer = self.configure_with_buffer()

        logging.getLogger("tests.component").info(
            "Žluťoučký kůň",
            extra={"event": "test_completed", "record_count": 3},
        )

        lines = buffer.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        event = json.loads(lines[0])
        self.assertEqual(event["schema"], LOG_SCHEMA)
        self.assertEqual(event["service"], "test-service")
        self.assertEqual(event["level"], "info")
        self.assertEqual(event["logger"], "tests.component")
        self.assertEqual(event["event"], "test_completed")
        self.assertEqual(event["message"], "Žluťoučký kůň")
        self.assertEqual(event["record_count"], 3)
        self.assertTrue(event["timestamp"].endswith("+00:00"))

    def test_configuration_is_idempotent_and_honors_log_level(self):
        buffer = self.configure_with_buffer()
        with patch.dict(os.environ, {"LOG_LEVEL": "WARNING"}):
            configure_logging("test-service")

        logger = logging.getLogger("tests.level")
        logger.info("hidden", extra={"event": "hidden"})
        logger.warning("visible", extra={"event": "visible"})

        lines = buffer.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["event"], "visible")

    def test_exception_details_are_json_encoded(self):
        buffer = self.configure_with_buffer()
        try:
            raise ValueError("bad value")
        except ValueError:
            logging.getLogger("tests.exception").exception(
                "Operation failed",
                extra={"event": "operation_failed", "error_type": "ValueError"},
            )

        event = json.loads(buffer.getvalue())
        self.assertEqual(event["error_type"], "ValueError")
        self.assertIn("ValueError: bad value", event["exception"])

    def test_log_message_accepts_readable_level_names(self):
        buffer = self.configure_with_buffer()

        log_message("Crawler warning", event="crawler_item_failed", level="warning")

        event = json.loads(buffer.getvalue())
        self.assertEqual(event["level"], "warning")
        self.assertEqual(event["event"], "crawler_item_failed")


if __name__ == "__main__":
    unittest.main()
