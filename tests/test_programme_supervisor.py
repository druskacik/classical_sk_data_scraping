import signal
import subprocess
import threading
import unittest
from unittest.mock import MagicMock, call, patch

from analyzers import programme_supervisor as supervisor


class ProgrammeSupervisorTests(unittest.TestCase):
    def config(self, **overrides):
        values = {
            "batch_timeout_seconds": 100,
            "stall_timeout_seconds": 20,
            "terminate_grace_seconds": 2,
        }
        values.update(overrides)
        return supervisor.ProgrammeSupervisorConfig(**values)

    def test_successful_child_uses_its_own_process_group(self):
        process = MagicMock(pid=321, returncode=0)
        process.poll.side_effect = [None, 0]
        stop_event = MagicMock()
        stop_event.wait.return_value = False
        with patch.object(supervisor.subprocess, "Popen", return_value=process) as popen:
            service = supervisor.ProgrammeAnalysisSupervisor(self.config())
            self.assertEqual(service.run(["python", "worker.py"], stop_event), 0)

        popen.assert_called_once_with(["python", "worker.py"], start_new_session=True)
        self.assertIsNone(service.process)

    def test_stalled_child_process_group_is_terminated(self):
        process = MagicMock(pid=321)
        process.poll.return_value = None
        process.wait.return_value = -signal.SIGTERM
        stop_event = MagicMock()
        stop_event.wait.return_value = False
        service = supervisor.ProgrammeAnalysisSupervisor(
            self.config(stall_timeout_seconds=5)
        )
        with (
            patch.object(supervisor.subprocess, "Popen", return_value=process),
            patch.object(supervisor, "monotonic", side_effect=[90, 100, 100]),
            patch.object(supervisor.os, "killpg") as killpg,
        ):
            self.assertEqual(service.run(["python"], stop_event), -signal.SIGTERM)

        killpg.assert_called_once_with(321, signal.SIGTERM)
        process.wait.assert_called_once_with(timeout=2)

    def test_batch_deadline_wins_despite_recent_heartbeat(self):
        process = MagicMock(pid=321)
        process.poll.return_value = None
        process.wait.return_value = -signal.SIGTERM
        stop_event = MagicMock()
        stop_event.wait.return_value = False
        service = supervisor.ProgrammeAnalysisSupervisor(
            self.config(batch_timeout_seconds=5, stall_timeout_seconds=20)
        )
        with (
            patch.object(supervisor.subprocess, "Popen", return_value=process),
            patch.object(supervisor, "monotonic", side_effect=[90, 100, 100]),
            patch.object(supervisor.os, "killpg") as killpg,
        ):
            self.assertEqual(service.run(["python"], stop_event), -signal.SIGTERM)

        killpg.assert_called_once_with(321, signal.SIGTERM)

    def test_sigkill_follows_termination_timeout(self):
        process = MagicMock(pid=321)
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("analyzer", 2), -signal.SIGKILL]
        stop_event = threading.Event()
        stop_event.set()
        service = supervisor.ProgrammeAnalysisSupervisor(self.config())
        with (
            patch.object(supervisor.subprocess, "Popen", return_value=process),
            patch.object(supervisor.os, "killpg") as killpg,
        ):
            self.assertEqual(service.run(["python"], stop_event), -signal.SIGKILL)

        self.assertEqual(
            killpg.call_args_list,
            [call(321, signal.SIGTERM), call(321, signal.SIGKILL)],
        )


if __name__ == "__main__":
    unittest.main()
