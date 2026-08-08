from __future__ import annotations

import logging
import os
import signal
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from time import monotonic


logger = logging.getLogger(__name__)

DEFAULT_BATCH_TIMEOUT_SECONDS = 20 * 60 * 60
DEFAULT_STALL_TIMEOUT_SECONDS = 40 * 60
DEFAULT_TERMINATE_GRACE_SECONDS = 30


@dataclass(frozen=True)
class ProgrammeSupervisorConfig:
    batch_timeout_seconds: int = DEFAULT_BATCH_TIMEOUT_SECONDS
    stall_timeout_seconds: int = DEFAULT_STALL_TIMEOUT_SECONDS
    terminate_grace_seconds: int = DEFAULT_TERMINATE_GRACE_SECONDS


class ProgrammeAnalysisSupervisor:
    def __init__(
        self,
        config: ProgrammeSupervisorConfig,
        *,
        heartbeat_path: Path | None = None,
    ) -> None:
        self.config = config
        self.heartbeat_path = heartbeat_path or Path(tempfile.gettempdir()) / (
            f"classical-programme-analysis-{os.getpid()}.heartbeat"
        )
        self.process: subprocess.Popen | None = None
        self.started_at: float | None = None
        self.last_progress_at: float | None = None
        self._heartbeat_mtime_ns: int | None = None

    def run(
        self,
        command: list[str],
        stop_event: threading.Event,
    ) -> int:
        if self.process is not None:
            raise RuntimeError("A programme analysis child is already running")
        self.heartbeat_path.unlink(missing_ok=True)
        self.process = subprocess.Popen(command, start_new_session=True)
        now = monotonic()
        self.started_at = now
        self.last_progress_at = now
        self._heartbeat_mtime_ns = None
        logger.info(
            "Started supervised programme analysis batch",
            extra={
                "event": "programme_analysis_supervisor_started",
                "child_pid": self.process.pid,
                "batch_timeout_seconds": self.config.batch_timeout_seconds,
                "stall_timeout_seconds": self.config.stall_timeout_seconds,
            },
        )
        try:
            while self.process.poll() is None:
                self._refresh_heartbeat()
                now = monotonic()
                if stop_event.wait(1):
                    return self._terminate(
                        "programme_analysis_supervisor_shutdown",
                        "Stopping programme analysis during service shutdown",
                    )
                if (
                    self.started_at is not None
                    and now - self.started_at >= self.config.batch_timeout_seconds
                ):
                    return self._terminate(
                        "programme_analysis_supervisor_batch_timed_out",
                        "Programme analysis exceeded its batch deadline",
                    )
                if (
                    self.last_progress_at is not None
                    and now - self.last_progress_at >= self.config.stall_timeout_seconds
                ):
                    return self._terminate(
                        "programme_analysis_supervisor_stalled",
                        "Programme analysis stopped reporting progress",
                    )
            return_code = self.process.returncode
            event = (
                "programme_analysis_supervisor_completed"
                if return_code == 0
                else "programme_analysis_supervisor_failed"
            )
            logger.log(
                logging.INFO if return_code == 0 else logging.ERROR,
                "Supervised programme analysis process exited",
                extra={
                    "event": event,
                    "child_pid": self.process.pid,
                    "return_code": return_code,
                    "duration_seconds": self._duration(),
                },
            )
            return return_code
        finally:
            self._clear_process()

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self._terminate(
                "programme_analysis_supervisor_shutdown",
                "Stopping programme analysis during service shutdown",
            )

    def _refresh_heartbeat(self) -> None:
        try:
            mtime_ns = self.heartbeat_path.stat().st_mtime_ns
        except FileNotFoundError:
            return
        if mtime_ns != self._heartbeat_mtime_ns:
            self._heartbeat_mtime_ns = mtime_ns
            self.last_progress_at = monotonic()

    def _terminate(self, event: str, message: str) -> int:
        process = self.process
        if process is None:
            return 0
        logger.error(
            message,
            extra={
                "event": event,
                "child_pid": process.pid,
                "duration_seconds": self._duration(),
            },
        )
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            return process.wait(timeout=self.config.terminate_grace_seconds)
        except subprocess.TimeoutExpired:
            logger.error(
                "Programme analysis ignored SIGTERM; killing its process group",
                extra={
                    "event": "programme_analysis_supervisor_forced_kill",
                    "child_pid": process.pid,
                    "terminate_grace_seconds": self.config.terminate_grace_seconds,
                },
            )
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            return process.wait()

    def _duration(self) -> float | None:
        if self.started_at is None:
            return None
        return round(monotonic() - self.started_at, 3)

    def _clear_process(self) -> None:
        self.process = None
        self.started_at = None
        self.last_progress_at = None
        self._heartbeat_mtime_ns = None
        self.heartbeat_path.unlink(missing_ok=True)
