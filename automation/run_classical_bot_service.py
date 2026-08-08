from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

from automation.run_programme_analyzer_worker import (
    ProgrammeAnalyzerWorker,
    WorkerConfig,
)
from deployment.deferred_deployment import (
    DeferredDeploymentConfig,
    DeferredDeploymentCoordinator,
)
from observability import configure_logging


logger = logging.getLogger(__name__)
TERMINATE_GRACE_SECONDS = 45


class ClassicalBotService:
    def __init__(
        self,
        worker: ProgrammeAnalyzerWorker,
        deployment: DeferredDeploymentCoordinator,
    ) -> None:
        self.worker = worker
        self.deployment = deployment
        self.shutdown_event = threading.Event()
        self.scheduler: subprocess.Popen | None = None
        self.scheduler_exit_code: int | None = None
        self.failed = False
        self.shutdown_requested = False

    def start_scheduler(self) -> None:
        command = [sys.executable, str(Path(__file__).parents[1] / "main.py"), "--scheduler-only"]
        self.scheduler = subprocess.Popen(command, start_new_session=True)
        logger.info(
            "Started daily scraper scheduler",
            extra={
                "event": "classical_bot_scheduler_started",
                "component": "scraper-scheduler",
                "child_pid": self.scheduler.pid,
            },
        )
        threading.Thread(target=self._monitor_scheduler, daemon=True).start()

    def _monitor_scheduler(self) -> None:
        scheduler = self.scheduler
        if scheduler is None:
            return
        self.scheduler_exit_code = scheduler.wait()
        if not self.shutdown_event.is_set():
            self.failed = True
            logger.error(
                "Daily scraper scheduler exited unexpectedly",
                extra={
                    "event": "classical_bot_scheduler_failed",
                    "component": "scraper-scheduler",
                    "child_pid": scheduler.pid,
                    "return_code": self.scheduler_exit_code,
                },
            )
            self.shutdown_event.set()
            self.worker.stop()

    def stop(self, signum: int, _frame: object = None) -> None:
        if self.shutdown_requested:
            return
        self.shutdown_requested = True
        logger.info(
            "Stopping classical-bot components",
            extra={"event": "classical_bot_shutdown_started", "signal": signum},
        )
        self.shutdown_event.set()
        self.worker.stop()
        self._signal_scheduler(signal.SIGTERM)

    def _signal_scheduler(self, signum: int) -> None:
        scheduler = self.scheduler
        if scheduler is None or scheduler.poll() is not None:
            return
        try:
            os.killpg(scheduler.pid, signum)
        except ProcessLookupError:
            pass

    def _finish_scheduler(self) -> None:
        scheduler = self.scheduler
        if scheduler is None or scheduler.poll() is not None:
            return
        self._signal_scheduler(signal.SIGTERM)
        try:
            scheduler.wait(timeout=TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            logger.error(
                "Daily scraper scheduler ignored SIGTERM; killing its process group",
                extra={
                    "event": "classical_bot_scheduler_forced_kill",
                    "component": "scraper-scheduler",
                    "child_pid": scheduler.pid,
                },
            )
            self._signal_scheduler(signal.SIGKILL)
            scheduler.wait()

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        self.start_scheduler()
        threading.Thread(
            target=self.deployment.monitor,
            args=(self.shutdown_event, self.worker.stop_event),
            daemon=True,
        ).start()
        try:
            self.worker.run()
            if self.deployment.pending_event.is_set() and not self.shutdown_event.is_set():
                self._deploy_when_drained()
            elif not self.shutdown_event.is_set():
                self.failed = True
                logger.error(
                    "Programme analyzer worker exited unexpectedly",
                    extra={
                        "event": "classical_bot_programme_worker_failed",
                        "component": "programme-analyzer",
                    },
                )
        except Exception:
            self.failed = True
            logger.exception(
                "Programme analyzer worker crashed",
                extra={
                    "event": "classical_bot_programme_worker_failed",
                    "component": "programme-analyzer",
                },
            )
        finally:
            self.shutdown_event.set()
            self.worker.stop()
            self._finish_scheduler()
        return 1 if self.failed else 0

    def _deploy_when_drained(self) -> None:
        logger.info(
            "Programme analyzer drained; requesting deployment",
            extra={
                "event": "deployment_drain_completed",
                "component": "deployment-coordinator",
            },
        )
        while not self.shutdown_event.is_set():
            if self.deployment.request_deployment():
                logger.info(
                    "Deployment requested; waiting for CapRover shutdown",
                    extra={
                        "event": "deployment_waiting_for_shutdown",
                        "component": "deployment-coordinator",
                    },
                )
                self.shutdown_event.wait()
                return
            self.shutdown_event.wait(self.deployment.config.retry_interval_seconds)


def main() -> None:
    configure_logging("classical-bot")
    try:
        worker_config = WorkerConfig.from_environment()
        deployment_config = DeferredDeploymentConfig.from_environment()
    except ValueError as error:
        raise SystemExit(str(error)) from error
    deployment = DeferredDeploymentCoordinator(deployment_config)
    worker = ProgrammeAnalyzerWorker(
        worker_config,
        stop_event=threading.Event(),
        drain_event=deployment.pending_event,
        before_batch=deployment.check_requested_update,
    )
    raise SystemExit(ClassicalBotService(worker, deployment).run())


if __name__ == "__main__":
    main()
