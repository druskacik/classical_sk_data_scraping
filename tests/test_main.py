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


if __name__ == "__main__":
    unittest.main()
