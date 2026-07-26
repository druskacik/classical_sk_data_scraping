import os
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import automation.run_crawler_factory_service as service


class ServiceConfigTests(unittest.TestCase):
    def test_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            config = service.ServiceConfig.from_environment()

        self.assertEqual(config.schedule_time.strftime("%H:%M"), "06:00")
        self.assertEqual(config.timezone.key, "Europe/Prague")
        self.assertEqual(config.update_interval_seconds, 300)
        self.assertEqual(config.max_urls, 5)
        self.assertEqual(config.timeout_minutes, 60)

    def test_invalid_schedule_is_rejected(self):
        with patch.dict(
            os.environ,
            {"CRAWLER_FACTORY_SCHEDULE_TIME": "25:00"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "HH:MM"):
                service.ServiceConfig.from_environment()

    def test_invalid_timezone_is_rejected(self):
        with patch.dict(
            os.environ,
            {"CRAWLER_FACTORY_TIMEZONE": "Europe/Nowhere"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "known timezone"):
                service.ServiceConfig.from_environment()

    def test_nonpositive_numeric_setting_is_rejected(self):
        with patch.dict(
            os.environ,
            {"CRAWLER_FACTORY_MAX_URLS": "0"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "at least 1"):
                service.ServiceConfig.from_environment()


class ScheduleTests(unittest.TestCase):
    def test_batch_is_due_after_schedule(self):
        now = datetime(2026, 7, 26, 6, 1, tzinfo=ZoneInfo("Europe/Prague"))

        self.assertTrue(service.batch_is_due(now, service.parse_schedule("06:00"), {}))

    def test_batch_is_not_due_before_schedule(self):
        now = datetime(2026, 7, 26, 5, 59, tzinfo=ZoneInfo("Europe/Prague"))

        self.assertFalse(service.batch_is_due(now, service.parse_schedule("06:00"), {}))

    def test_batch_is_not_repeated_on_same_date(self):
        now = datetime(2026, 7, 26, 12, 0, tzinfo=ZoneInfo("Europe/Prague"))
        state = {"last_factory_attempt_date": "2026-07-26"}

        self.assertFalse(service.batch_is_due(now, service.parse_schedule("06:00"), state))

    def test_batch_uses_local_date_after_daylight_saving_change(self):
        now = datetime(2026, 10, 25, 6, 0, tzinfo=ZoneInfo("Europe/Prague"))
        state = {"last_factory_attempt_date": "2026-10-24"}

        self.assertTrue(service.batch_is_due(now, service.parse_schedule("06:00"), state))


class FactoryServiceTests(unittest.TestCase):
    def config(self, root: Path, webhook: str | None = "https://captain.test/hook"):
        return service.ServiceConfig(
            repository="https://github.com/example/repository.git",
            schedule_time=service.parse_schedule("06:00"),
            timezone=ZoneInfo("Europe/Prague"),
            update_interval_seconds=300,
            deploy_webhook=webhook,
            max_urls=7,
            timeout_minutes=45,
            state_path=root / "service-state.json",
        )

    def test_factory_attempt_is_persisted_before_child_starts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            supervisor = service.FactoryService(self.config(root))
            process = Mock()
            process.poll.return_value = 0
            process.returncode = 0

            with patch.object(subprocess, "Popen", return_value=process) as popen:
                result = supervisor.run_factory(
                    datetime(2026, 7, 26, 6, 0, tzinfo=ZoneInfo("Europe/Prague"))
                )

            state = service.load_service_state(root / "service-state.json")

        self.assertEqual(result, 0)
        self.assertEqual(state["last_factory_attempt_date"], "2026-07-26")
        command = popen.call_args.args[0]
        self.assertIn("https://github.com/example/repository.git", command)
        self.assertEqual(command[command.index("--max-urls") + 1], "7")
        self.assertEqual(command[command.index("--timeout-minutes") + 1], "45")

    def test_matching_commit_does_not_request_deployment(self):
        with tempfile.TemporaryDirectory() as temporary:
            supervisor = service.FactoryService(self.config(Path(temporary)))
            with (
                patch.dict(os.environ, {"CAPROVER_GIT_COMMIT_SHA": "abc"}, clear=True),
                patch.object(service, "remote_commit", return_value="abc"),
                patch.object(service, "request_deployment") as deploy,
            ):
                self.assertFalse(supervisor.check_for_update())

        deploy.assert_not_called()

    def test_new_commit_requests_deployment(self):
        with tempfile.TemporaryDirectory() as temporary:
            supervisor = service.FactoryService(self.config(Path(temporary)))
            with (
                patch.dict(os.environ, {"CAPROVER_GIT_COMMIT_SHA": "old"}, clear=True),
                patch.object(service, "remote_commit", return_value="new"),
                patch.object(service, "request_deployment") as deploy,
            ):
                self.assertTrue(supervisor.check_for_update(monotonic_now=100))

        deploy.assert_called_once_with("https://captain.test/hook")

    def test_successful_request_is_suppressed_during_cooldown(self):
        with tempfile.TemporaryDirectory() as temporary:
            supervisor = service.FactoryService(self.config(Path(temporary)))
            with (
                patch.dict(os.environ, {"CAPROVER_GIT_COMMIT_SHA": "old"}, clear=True),
                patch.object(service, "remote_commit", return_value="new"),
                patch.object(service, "request_deployment") as deploy,
            ):
                self.assertTrue(supervisor.check_for_update(monotonic_now=100))
                self.assertFalse(supervisor.check_for_update(monotonic_now=200))

        deploy.assert_called_once()

    def test_failed_request_can_retry_on_next_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            supervisor = service.FactoryService(self.config(Path(temporary)))
            with (
                patch.dict(os.environ, {"CAPROVER_GIT_COMMIT_SHA": "old"}, clear=True),
                patch.object(service, "remote_commit", return_value="new"),
                patch.object(
                    service,
                    "request_deployment",
                    side_effect=[RuntimeError("failed"), None],
                ) as deploy,
            ):
                self.assertFalse(supervisor.check_for_update(monotonic_now=100))
                self.assertTrue(supervisor.check_for_update(monotonic_now=200))

        self.assertEqual(deploy.call_count, 2)

    def test_update_is_skipped_while_factory_child_is_active(self):
        with tempfile.TemporaryDirectory() as temporary:
            supervisor = service.FactoryService(self.config(Path(temporary)))
            supervisor.child = Mock()
            supervisor.child.poll.return_value = None
            with patch.object(service, "remote_commit") as remote:
                self.assertFalse(supervisor.check_for_update())

        remote.assert_not_called()

    def test_stop_signal_is_forwarded_to_active_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            supervisor = service.FactoryService(self.config(Path(temporary)))
            supervisor.child = Mock()
            supervisor.child.poll.return_value = None

            supervisor.stop(service.signal.SIGTERM)

        self.assertTrue(supervisor.stop_event.is_set())
        supervisor.child.send_signal.assert_called_once_with(service.signal.SIGTERM)

    def test_missing_webhook_disables_updates(self):
        with tempfile.TemporaryDirectory() as temporary:
            supervisor = service.FactoryService(self.config(Path(temporary), webhook=None))
            with (
                patch.dict(os.environ, {"CAPROVER_GIT_COMMIT_SHA": "old"}, clear=True),
                patch.object(service, "remote_commit") as remote,
            ):
                self.assertFalse(supervisor.check_for_update())

        remote.assert_not_called()

    def test_git_authentication_is_configured_when_token_is_available(self):
        with (
            patch.dict(os.environ, {"GH_TOKEN": "secret"}, clear=True),
            patch.object(subprocess, "run") as run,
        ):
            service.prepare_git_authentication()

        run.assert_called_once_with(
            ["gh", "auth", "setup-git"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )


if __name__ == "__main__":
    unittest.main()
