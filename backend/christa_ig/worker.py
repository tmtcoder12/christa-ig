"""Durable low-volume worker backed by Supabase job tables."""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import uuid
from datetime import datetime, timedelta, timezone

from .config import get_settings
from .observability import configure_logging, log_event
from .webhook_events import retry_delay

logger = logging.getLogger(__name__)
RETRYABLE_RESULTS = {"db_error", "comment_automation_requires_database"}
IGNORED_RESULT_MARKERS = ("ignored", "skipped", "not_configured", "not_connected", "no_")


class RetryableJobError(RuntimeError):
    """A known transient workflow outcome that is safe to run again."""


class Worker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.stop_event = threading.Event()
        self.last_cleanup_at: datetime | None = None

    def stop(self, *_args) -> None:
        self.stop_event.set()

    def process_webhook_jobs(self) -> int:
        from supabase_client import (
            claim_webhook_jobs,
            complete_webhook_job,
            fail_webhook_job,
            retry_webhook_job,
        )
        from workflows import process_webhook_payload

        from .factory import create_app

        jobs = claim_webhook_jobs(
            self.settings.worker_batch_size,
            self.worker_id,
            self.settings.worker_lease_seconds,
        )
        if not jobs:
            return 0
        app = create_app()
        for job in jobs:
            try:
                with app.app_context():
                    result = process_webhook_payload(job.get("payload") or {})
                outcome = str(result.get("processing_result") or "ignored")
                if outcome in RETRYABLE_RESULTS:
                    raise RetryableJobError(outcome)
                status = "ignored" if any(marker in outcome for marker in IGNORED_RESULT_MARKERS) else "succeeded"
                complete_webhook_job(job["id"], status=status)
                log_event(
                    logger,
                    "webhook_job_completed",
                    request_id=job.get("request_id"),
                    job_id=job["id"],
                    external_event_id=job["external_event_id"],
                    outcome=outcome,
                )
            except Exception as exc:  # noqa: BLE001 - job state must always be released.
                attempts = int(job.get("attempt_count") or 1)
                retryable = isinstance(exc, RetryableJobError)
                if not retryable or attempts >= self.settings.worker_max_attempts:
                    error_code = "max_attempts" if retryable else "unexpected_error"
                    fail_webhook_job(job["id"], error_code, str(exc))
                    terminal = True
                else:
                    available_at = datetime.now(timezone.utc) + timedelta(seconds=retry_delay(attempts))
                    retry_webhook_job(job["id"], available_at.isoformat(), "retryable_error", str(exc))
                    terminal = False
                log_event(
                    logger,
                    "webhook_job_failed",
                    level=logging.ERROR,
                    request_id=job.get("request_id"),
                    job_id=job["id"],
                    attempt_count=attempts,
                    terminal=terminal,
                    error_type=type(exc).__name__,
                )
        return len(jobs)

    def process_promotion_setups(self) -> int:
        from supabase_client import claim_promotion_setups, update_promotion_setup
        from workflows import process_promotion_setup_tick

        setups = claim_promotion_setups(
            min(self.settings.worker_batch_size, 20),
            self.worker_id,
            self.settings.worker_lease_seconds,
        )
        for setup in setups:
            try:
                process_promotion_setup_tick(setup)
            except Exception as exc:  # noqa: BLE001 - release the durable lease on every failure.
                update_promotion_setup(
                    setup["id"],
                    {
                        "status": "polling",
                        "next_poll_at": (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
                        "locked_at": None,
                        "locked_until": None,
                        "locked_by": None,
                        "last_worker_error": str(exc)[:2000],
                    },
                )
                log_event(
                    logger,
                    "promotion_poll_failed",
                    level=logging.ERROR,
                    setup_id=setup["id"],
                    error_type=type(exc).__name__,
                )
        return len(setups)

    def process_followups(self) -> int:
        from supabase_client import list_due_sms_messages
        from workflows import process_due_sms_message

        messages = list_due_sms_messages(limit=self.settings.worker_batch_size)
        for message in messages:
            process_due_sms_message(message)
        return len(messages)

    def cleanup_failed_jobs(self) -> None:
        from supabase_client import delete_expired_failed_webhook_jobs

        now = datetime.now(timezone.utc)
        if self.last_cleanup_at and now - self.last_cleanup_at < timedelta(hours=1):
            return
        delete_expired_failed_webhook_jobs(self.settings.webhook_failure_retention_days)
        self.last_cleanup_at = now

    def run(self) -> None:
        configure_logging(self.settings.log_level)
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        log_event(logger, "worker_started", worker_id=self.worker_id)
        while not self.stop_event.is_set():
            processed = 0
            try:
                processed += self.process_webhook_jobs()
                processed += self.process_promotion_setups()
                processed += self.process_followups()
                self.cleanup_failed_jobs()
            except Exception as exc:  # noqa: BLE001 - keep the long-running worker alive.
                log_event(
                    logger,
                    "worker_iteration_failed",
                    level=logging.ERROR,
                    worker_id=self.worker_id,
                    error_type=type(exc).__name__,
                )
            if processed == 0:
                self.stop_event.wait(self.settings.worker_poll_seconds)
        log_event(logger, "worker_stopped", worker_id=self.worker_id)


def main() -> None:
    Worker().run()


if __name__ == "__main__":
    main()
