from __future__ import annotations

import hashlib
import hmac
import json

import workflows as app_module
from christa_ig.config import reset_settings_cache
from christa_ig.factory import create_app


def _signed_headers(body: bytes, secret: str = "test-meta-secret") -> dict[str, str]:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={digest}"}


def test_health_routes_are_available():
    client = create_app().test_client()
    assert client.get("/").status_code == 200
    assert client.get("/health/live").get_json() == {"status": "ok"}


def test_webhook_rejects_invalid_signature(monkeypatch):
    enqueue_called = False

    def fake_enqueue(*_args, **_kwargs):
        nonlocal enqueue_called
        enqueue_called = True

    monkeypatch.setattr(app_module, "enqueue_webhook_jobs", fake_enqueue)
    client = create_app().test_client()
    response = client.post("/webhook", data=b"{}", headers={"Content-Type": "application/json"})
    assert response.status_code == 401
    assert enqueue_called is False


def test_webhook_enqueues_without_processing_external_services(monkeypatch):
    monkeypatch.setenv("META_APP_SECRET", "test-meta-secret")
    reset_settings_cache()
    body = json.dumps(
        {
            "object": "instagram",
            "entry": [{"id": "business", "messaging": [{"message": {"mid": "m1", "text": "hello"}}]}],
        },
        separators=(",", ":"),
    ).encode()
    captured = []
    monkeypatch.setattr(app_module, "is_supabase_configured", lambda: True)
    monkeypatch.setattr(app_module, "enqueue_webhook_jobs", lambda jobs, request_id=None: captured.extend(jobs) or jobs)
    monkeypatch.setattr(
        app_module,
        "process_webhook_payload",
        lambda _payload: (_ for _ in ()).throw(AssertionError("request path processed the job")),
    )
    client = create_app().test_client()
    response = client.post("/webhook", data=body, headers=_signed_headers(body))
    assert response.status_code == 200
    assert response.get_data(as_text=True) == "OK"
    assert [job["external_event_id"] for job in captured] == ["dm:m1"]


def test_webhook_returns_503_when_queue_is_unavailable(monkeypatch):
    body = b'{"entry":[{"id":"business","messaging":[{"message":{"mid":"m1","text":"hello"}}]}]}'
    monkeypatch.setattr(app_module, "is_supabase_configured", lambda: False)
    client = create_app().test_client()
    response = client.post("/webhook", data=body, headers=_signed_headers(body))
    assert response.status_code == 503


def test_request_body_limit_returns_consistent_error():
    response = (
        create_app()
        .test_client()
        .post(
            "/webhook",
            data=b"x" * (1_048_576 + 1),
            headers={"Content-Type": "application/json"},
        )
    )
    assert response.status_code == 413
    assert response.get_json()["code"] == "request_too_large"
    assert response.get_json()["request_id"]


def test_unknown_api_routes_return_consistent_errors():
    response = create_app().test_client().get("/api/not-a-route")
    assert response.status_code == 404
    assert response.get_json()["code"] == "http_404"
    assert response.get_json()["request_id"]


def test_validation_limits():
    with create_app().test_request_context("/"):
        try:
            app_module.validate_promotion_payload(
                {
                    "instagram_account_id": "a",
                    "comment_trigger_mode": "keywords",
                    "trigger_keywords": ["x" * 101],
                    "comment_reply_text": "reply",
                }
            )
        except ValueError as exc:
            assert "100 characters" in str(exc)
        else:
            raise AssertionError("Expected keyword validation to fail")


def test_phone_normalization():
    assert app_module.normalize_phone_number("(604) 555-1212") == "+16045551212"
    assert app_module.normalize_phone_number("invalid") is None


def test_twilio_validation_uses_proxy_aware_public_url(monkeypatch):
    captured = {}

    class FakeValidator:
        def __init__(self, token):
            captured["token"] = token

        def validate(self, url, form, signature):
            captured.update(url=url, form=form, signature=signature)
            return True

    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "twilio-secret")
    monkeypatch.setattr(app_module, "RequestValidator", FakeValidator)
    monkeypatch.setattr(app_module, "is_supabase_configured", lambda: False)
    response = (
        create_app()
        .test_client()
        .post(
            "http://internal:5000/api/twilio/sms-webhook",
            data={"Body": "STOP"},
            headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "api.example.com", "X-Twilio-Signature": "sig"},
        )
    )

    assert response.status_code == 200
    assert captured["url"] == "https://api.example.com/api/twilio/sms-webhook"
    assert captured["form"] == {"Body": "STOP"}
