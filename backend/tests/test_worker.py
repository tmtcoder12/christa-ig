from __future__ import annotations

from types import SimpleNamespace

import supabase_client
import workflows as app_module
from christa_ig.worker import Worker


def _worker(max_attempts=5):
    worker = Worker()
    worker.settings = SimpleNamespace(
        worker_batch_size=10,
        worker_lease_seconds=300,
        worker_max_attempts=max_attempts,
        webhook_failure_retention_days=7,
        worker_poll_seconds=1,
        log_level="INFO",
    )
    return worker


def test_webhook_job_completes_once(monkeypatch):
    job = {
        "id": "job-1",
        "external_event_id": "dm:m1",
        "payload": {"entry": []},
        "attempt_count": 1,
        "request_id": "request-1",
    }
    completed = []
    claimed_batches = iter(([job], []))
    monkeypatch.setattr(supabase_client, "claim_webhook_jobs", lambda *_args: next(claimed_batches))
    monkeypatch.setattr(
        supabase_client, "complete_webhook_job", lambda *args, **kwargs: completed.append((args, kwargs))
    )
    monkeypatch.setattr(supabase_client, "fail_webhook_job", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(supabase_client, "retry_webhook_job", lambda *_args, **_kwargs: None)
    actions = []
    monkeypatch.setattr(
        app_module,
        "process_webhook_payload",
        lambda payload: actions.append(payload) or {"processing_result": "replied"},
    )
    worker = _worker()
    assert worker.process_webhook_jobs() == 1
    assert worker.process_webhook_jobs() == 0
    assert len(actions) == 1
    assert completed[0][0][0] == "job-1"
    assert completed[0][1]["status"] == "succeeded"


def test_webhook_job_retries_then_fails_at_limit(monkeypatch):
    retries = []
    failures = []
    monkeypatch.setattr(app_module, "process_webhook_payload", lambda _payload: {"processing_result": "db_error"})
    monkeypatch.setattr(supabase_client, "complete_webhook_job", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(supabase_client, "retry_webhook_job", lambda *args: retries.append(args))
    monkeypatch.setattr(supabase_client, "fail_webhook_job", lambda *args: failures.append(args))

    retry_job = {"id": "retry", "external_event_id": "dm:m1", "payload": {}, "attempt_count": 1}
    monkeypatch.setattr(supabase_client, "claim_webhook_jobs", lambda *_args: [retry_job])
    _worker().process_webhook_jobs()
    assert retries and not failures

    terminal_job = {**retry_job, "id": "terminal", "attempt_count": 5}
    monkeypatch.setattr(supabase_client, "claim_webhook_jobs", lambda *_args: [terminal_job])
    _worker().process_webhook_jobs()
    assert failures[0][0] == "terminal"


def test_unexpected_webhook_errors_fail_without_retry(monkeypatch):
    retries = []
    failures = []
    monkeypatch.setattr(
        app_module,
        "process_webhook_payload",
        lambda _payload: (_ for _ in ()).throw(ValueError("bad workflow state")),
    )
    monkeypatch.setattr(
        supabase_client,
        "claim_webhook_jobs",
        lambda *_args: [
            {
                "id": "job-1",
                "external_event_id": "dm:m1",
                "payload": {},
                "attempt_count": 1,
            }
        ],
    )
    monkeypatch.setattr(supabase_client, "complete_webhook_job", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(supabase_client, "retry_webhook_job", lambda *args: retries.append(args))
    monkeypatch.setattr(supabase_client, "fail_webhook_job", lambda *args: failures.append(args))

    _worker().process_webhook_jobs()
    assert not retries
    assert failures[0][1] == "unexpected_error"


def test_promotion_and_followup_work_are_delegated(monkeypatch):
    setup = {"id": "setup-1"}
    message = {"id": "sms-1"}
    promotion_calls = []
    followup_calls = []
    monkeypatch.setattr(supabase_client, "claim_promotion_setups", lambda *_args: [setup])
    monkeypatch.setattr(supabase_client, "update_promotion_setup", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(supabase_client, "list_due_sms_messages", lambda **_kwargs: [message])
    monkeypatch.setattr(app_module, "process_promotion_setup_tick", lambda value: promotion_calls.append(value))
    monkeypatch.setattr(app_module, "process_due_sms_message", lambda value: followup_calls.append(value))
    worker = _worker()
    assert worker.process_promotion_setups() == 1
    assert worker.process_followups() == 1
    assert promotion_calls == [setup]
    assert followup_calls == [message]


def test_promotion_error_releases_lease(monkeypatch):
    updates = []
    monkeypatch.setattr(supabase_client, "claim_promotion_setups", lambda *_args: [{"id": "setup-1"}])
    monkeypatch.setattr(supabase_client, "update_promotion_setup", lambda *args: updates.append(args))
    monkeypatch.setattr(
        app_module,
        "process_promotion_setup_tick",
        lambda _value: (_ for _ in ()).throw(RuntimeError("temporary")),
    )
    assert _worker().process_promotion_setups() == 1
    assert updates[0][1]["locked_until"] is None


def test_cleanup_runs_at_most_hourly(monkeypatch):
    calls = []
    monkeypatch.setattr(supabase_client, "delete_expired_failed_webhook_jobs", lambda days: calls.append(days))
    worker = _worker()
    worker.cleanup_failed_jobs()
    worker.cleanup_failed_jobs()
    assert calls == [7]


def test_worker_can_stop_before_loop(monkeypatch):
    worker = _worker()
    worker.stop()
    monkeypatch.setattr("signal.signal", lambda *_args: None)
    worker.run()
    assert worker.stop_event.is_set()
