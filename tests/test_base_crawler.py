import unittest
from unittest.mock import patch

from crawlers.base import BaseCrawler, CrawlerConfig


class FailingCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug="failing_example",
        source="Failing example",
        source_url="https://example.com/",
    )

    def scrape(self):
        raise RuntimeError("source unavailable")


class BaseCrawlerLoggingTests(unittest.TestCase):
    def test_run_logs_structured_failure_and_reraises(self):
        crawler = FailingCrawler()

        with (
            patch("crawlers.base.configure_logging"),
            self.assertLogs("crawlers.base", level="ERROR") as captured,
        ):
            with self.assertRaisesRegex(RuntimeError, "source unavailable"):
                crawler.run()

        self.assertEqual(len(captured.records), 1)
        record = captured.records[0]
        self.assertEqual(record.event, "crawler_failed")
        self.assertEqual(record.crawler, "failing_example")
        self.assertEqual(record.source_url, "https://example.com/")
        self.assertEqual(record.error_type, "RuntimeError")
        self.assertEqual(record.error_message, "source unavailable")
        self.assertIsNotNone(record.exc_info)


if __name__ == "__main__":
    unittest.main()
