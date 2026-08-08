import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation.codex_auth import (
    CodexAuthPause,
    CodexAuthRequiredError,
    codex_auth_reason,
    raise_for_codex_auth,
)


class CodexAuthClassificationTests(unittest.TestCase):
    def test_revoked_refresh_token_is_auth_failure(self):
        self.assertEqual(
            codex_auth_reason(
                "Your access token could not be refreshed because your refresh token was "
                "revoked. Please log out and sign in again."
            ),
            "refresh_token_revoked",
        )

    def test_generic_refresh_failure_is_not_assumed_to_be_auth_failure(self):
        self.assertIsNone(codex_auth_reason("Access token could not be refreshed"))

    def test_generic_sign_in_instruction_is_not_assumed_to_be_auth_failure(self):
        self.assertIsNone(codex_auth_reason("Please log out and sign in again"))

    def test_openai_401_is_auth_failure(self):
        self.assertEqual(
            codex_auth_reason(
                "unexpected status 401 Unauthorized, url: https://api.openai.com/v1/responses"
            ),
            "openai_unauthorized",
        )

    def test_target_website_401_is_not_auth_failure(self):
        self.assertIsNone(
            codex_auth_reason("401 Unauthorized from https://tickets.example.org/events")
        )

    def test_nested_auth_exception_is_detected(self):
        try:
            try:
                raise RuntimeError("Missing bearer or basic authentication in header")
            except RuntimeError as cause:
                raise RuntimeError("Codex turn failed") from cause
        except RuntimeError as error:
            with self.assertRaises(CodexAuthRequiredError) as raised:
                raise_for_codex_auth(error)
        self.assertEqual(raised.exception.reason_code, "missing_api_auth")


class CodexAuthPauseTests(unittest.TestCase):
    def test_unreadable_existing_marker_remains_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "pause.json"
            path.write_text("not-json", encoding="utf-8")
            pause = CodexAuthPause.for_service("classical-bot", path)

            self.assertTrue(pause.is_paused())
            self.assertFalse(pause.pause("refresh_token_revoked"))

    def test_pause_persists_and_successful_smoke_resumes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex_home = root / "codex"
            codex_home.mkdir()
            auth = codex_home / "auth.json"
            auth.write_text("first", encoding="utf-8")
            pause = CodexAuthPause.for_service("classical-bot", root / "pause.json")
            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                self.assertTrue(pause.pause("refresh_token_revoked"))
                self.assertFalse(pause.pause("refresh_token_revoked"))
                self.assertFalse(pause.auth_file_changed())
                auth.write_text("second-value", encoding="utf-8")
                completed = subprocess.CompletedProcess([], 0, stdout="{}", stderr="")
                with patch("automation.codex_auth.subprocess.run", return_value=completed):
                    resumed, failure = pause.verify_and_resume(cwd=root)

            self.assertTrue(resumed)
            self.assertIsNone(failure)
            self.assertFalse(pause.path.exists())

    def test_failed_auth_smoke_keeps_pause_and_records_new_signature(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex_home = root / "codex"
            codex_home.mkdir()
            auth = codex_home / "auth.json"
            auth.write_text("first", encoding="utf-8")
            pause = CodexAuthPause.for_service("classical-bot", root / "pause.json")
            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                pause.pause("refresh_token_revoked")
                auth.write_text("second-value", encoding="utf-8")
                completed = subprocess.CompletedProcess(
                    [],
                    1,
                    stdout="",
                    stderr=(
                        "Your access token could not be refreshed because your refresh token "
                        "was revoked. Please log out and sign in again."
                    ),
                )
                with patch("automation.codex_auth.subprocess.run", return_value=completed):
                    resumed, failure = pause.verify_and_resume(cwd=root)
                self.assertFalse(pause.auth_file_changed())

            self.assertFalse(resumed)
            self.assertEqual(failure, "refresh_token_revoked")
            self.assertTrue(pause.path.exists())


if __name__ == "__main__":
    unittest.main()
