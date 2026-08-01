import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import deployment.caprover_updater as caprover
import deployment.scraper_updater as updater


OLD_SHA = "a" * 40
NEW_SHA = "b" * 40


class ScraperUpdaterTests(unittest.TestCase):
    def config(self, root: Path, webhook: str | None = "https://captain.test/hook"):
        return updater.UpdaterConfig(
            repository="https://github.com/example/repository.git",
            deploy_webhook=webhook,
            state_path=root / "deployment-state.json",
        )

    def test_update_is_blocked_until_daily_pipeline_finishes(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = updater.ScraperUpdater(self.config(Path(temporary)))
            with (
                patch.dict(os.environ, {"CAPROVER_GIT_COMMIT_SHA": OLD_SHA}, clear=True),
                patch.object(caprover, "remote_commit", return_value=NEW_SHA) as remote,
                patch.object(caprover, "request_deployment") as deploy,
            ):
                self.assertFalse(service.check_for_update())
                service.begin_daily_pipeline()
                self.assertFalse(service.check_for_update())
                service.finish_daily_pipeline()

        remote.assert_called_once()
        deploy.assert_called_once_with("https://captain.test/hook")

    def test_failed_webhook_remains_retryable_while_idle(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = updater.ScraperUpdater(self.config(Path(temporary)))
            service.updates_enabled = True
            with (
                patch.dict(os.environ, {"CAPROVER_GIT_COMMIT_SHA": OLD_SHA}, clear=True),
                patch.object(caprover, "remote_commit", return_value=NEW_SHA),
                patch.object(
                    caprover,
                    "request_deployment",
                    side_effect=[RuntimeError("failed"), None],
                ) as deploy,
            ):
                self.assertFalse(service.check_for_update())
                self.assertTrue(service.check_for_update())

        self.assertEqual(deploy.call_count, 2)

    def test_successful_request_is_suppressed_after_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self.config(root)
            service = updater.ScraperUpdater(config)
            service.updates_enabled = True
            with (
                patch.dict(os.environ, {"CAPROVER_GIT_COMMIT_SHA": OLD_SHA}, clear=True),
                patch.object(caprover, "remote_commit", return_value=NEW_SHA),
                patch.object(caprover, "request_deployment") as deploy,
            ):
                self.assertTrue(service.check_for_update())
                restarted = updater.ScraperUpdater(config)
                restarted.updates_enabled = True
                self.assertFalse(restarted.check_for_update())

        deploy.assert_called_once()

    def test_matching_commit_does_not_deploy(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = updater.ScraperUpdater(self.config(Path(temporary)))
            service.updates_enabled = True
            with (
                patch.dict(os.environ, {"CAPROVER_GIT_COMMIT_SHA": OLD_SHA}, clear=True),
                patch.object(caprover, "remote_commit", return_value=OLD_SHA),
                patch.object(caprover, "request_deployment") as deploy,
            ):
                self.assertFalse(service.check_for_update())
                self.assertFalse(service.updates_enabled)

        deploy.assert_not_called()

    def test_old_successful_request_can_be_retried_after_failed_deployment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            caprover.save_state(
                root / "deployment-state.json",
                {
                    "last_deploy_request_sha": NEW_SHA,
                    "last_deploy_request_at": (
                        datetime.now(UTC) - timedelta(hours=1)
                    ).isoformat(),
                },
            )
            service = updater.ScraperUpdater(self.config(root))
            service.updates_enabled = True
            with (
                patch.dict(os.environ, {"CAPROVER_GIT_COMMIT_SHA": OLD_SHA}, clear=True),
                patch.object(caprover, "remote_commit", return_value=NEW_SHA),
                patch.object(caprover, "request_deployment") as deploy,
            ):
                self.assertTrue(service.check_for_update())

        deploy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
