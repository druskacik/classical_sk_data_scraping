import unittest
from unittest.mock import patch

from crawlers.sk.cultusruzinov_sk.main import CultusRuzinovCrawler


class CultusRuzinovCrawlerTests(unittest.TestCase):
    @patch("crawlers.sk.cultusruzinov_sk.main.log_message")
    @patch("crawlers.sk.cultusruzinov_sk.main.get_event_data")
    @patch("crawlers.sk.cultusruzinov_sk.main.get_event_slugs")
    @patch("crawlers.sk.cultusruzinov_sk.main.get_access_token")
    def test_one_broken_event_does_not_fail_the_whole_source(
        self,
        get_access_token,
        get_event_slugs,
        get_event_data,
        log_message,
    ):
        get_access_token.return_value = "token"
        get_event_slugs.return_value = ["good-event", "deleted-event"]
        good_event = {"title": "Concert"}
        get_event_data.side_effect = [good_event, KeyError("event")]

        self.assertEqual(CultusRuzinovCrawler().scrape(), [good_event])

        log_message.assert_any_call(
            "Event detail failed",
            event="crawler_item_failed",
            level="warning",
            slug="deleted-event",
            error_type="KeyError",
            error_message="'event'",
        )


if __name__ == "__main__":
    unittest.main()
