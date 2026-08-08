import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import deployment.scraper_updater as updater


class ScraperUpdaterTests(unittest.TestCase):
    def test_update_check_is_requested_only_after_daily_pipeline(self):
        with tempfile.TemporaryDirectory() as temporary:
            request_path = Path(temporary) / "update-request.json"
            service = updater.ScraperUpdater(updater.UpdaterConfig(request_path))

            self.assertFalse(service.request_update_check())
            service.begin_daily_pipeline()
            self.assertFalse(service.request_update_check())
            service.finish_daily_pipeline()

            self.assertTrue(request_path.exists())
            self.assertFalse(service.request_pending)

    def test_failed_marker_write_remains_retryable(self):
        with tempfile.TemporaryDirectory() as temporary:
            request_path = Path(temporary) / "update-request.json"
            service = updater.ScraperUpdater(updater.UpdaterConfig(request_path))
            service.request_pending = True
            with patch.object(Path, "write_text", side_effect=OSError("disk full")):
                self.assertFalse(service.request_update_check())
            self.assertTrue(service.request_pending)

            self.assertTrue(service.request_update_check())
            self.assertTrue(request_path.exists())

    def test_starting_next_pipeline_cancels_a_stale_local_retry(self):
        service = updater.ScraperUpdater(updater.UpdaterConfig(Path("unused")))
        service.request_pending = True
        service.begin_daily_pipeline()
        self.assertFalse(service.request_pending)


if __name__ == "__main__":
    unittest.main()
