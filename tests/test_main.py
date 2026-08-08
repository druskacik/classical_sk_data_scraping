import os
import unittest
from unittest.mock import patch

import main


class MainTests(unittest.TestCase):
    def test_jobs_do_not_run_on_startup_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(main.should_run_jobs_on_startup())

    def test_jobs_can_be_enabled_on_startup(self):
        for value in ("1", "true", "TRUE", "yes", "on"):
            with self.subTest(value=value), patch.dict(
                os.environ, {main.RUN_JOBS_ON_STARTUP_ENV: value}, clear=True
            ):
                self.assertTrue(main.should_run_jobs_on_startup())

    def test_unrecognized_value_keeps_startup_run_disabled(self):
        with patch.dict(os.environ, {main.RUN_JOBS_ON_STARTUP_ENV: "no"}, clear=True):
            self.assertFalse(main.should_run_jobs_on_startup())

    def test_scheduler_only_argument_runs_daily_scheduler(self):
        with (
            patch.object(main.sys, "argv", ["main.py", "--scheduler-only"]),
            patch.object(main, "scheduler_main") as scheduler_main,
        ):
            main.main()
        scheduler_main.assert_called_once_with()

    def test_default_entrypoint_runs_combined_service(self):
        with (
            patch.object(main.sys, "argv", ["main.py"]),
            patch("automation.run_classical_bot_service.main") as service_main,
        ):
            main.main()
        service_main.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
