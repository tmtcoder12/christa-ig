from __future__ import annotations

import pytest
import workflows
from christa_ig.factory import create_app


def test_message_and_comment_parsers_reject_echoes_and_extract_events():
    echo = {"entry": [{"id": "account", "messaging": [{"sender": {"id": "account"}, "message": {"text": "hi"}}]}]}
    assert workflows.get_dm_processing_info(echo)["reason"] == "skipped_echo"

    comment = {
        "entry": [
            {
                "id": "account",
                "changes": [
                    {
                        "value": {
                            "id": "comment-1",
                            "text": "MENU please",
                            "media": {"id": "media-1"},
                            "from": {"id": "customer-1", "username": "customer"},
                        }
                    }
                ],
            }
        ]
    }
    parsed = workflows.get_comment_processing_info(comment)
    assert parsed["should_process"] is True
    assert parsed["comment_id"] == "comment-1"


def test_keyword_classification_short_circuits_openai(monkeypatch):
    monkeypatch.setattr(
        workflows,
        "classify_restaurant_comment_for_promo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("classifier should not run")),
    )
    result = workflows.resolve_comment_automation_trigger(
        "Can you DM the offer?",
        {"comment_trigger_mode": "keywords_or_restaurant_intent", "trigger_keywords": ["DM"]},
        {"business_id": "business-1"},
        {"id": "comment-1"},
    )
    assert result["matched"] is True
    assert result["trigger_source"] == "keyword"


def test_restaurant_classifier_failures_and_negative_categories_fail_closed(monkeypatch):
    monkeypatch.setattr(workflows, "store_comment_classification", lambda *_args: {"id": "classification-1"})
    monkeypatch.setattr(
        workflows,
        "classify_restaurant_comment_for_promo",
        lambda *_args, **_kwargs: {
            "success": True,
            "should_trigger": True,
            "confidence": 0.99,
            "category": "complaint or negative",
        },
    )
    result = workflows.resolve_comment_automation_trigger(
        "This was terrible",
        {"comment_trigger_mode": "restaurant_intent", "trigger_keywords": []},
        {"business_id": "business-1"},
        {"id": "comment-1"},
    )
    assert result["matched"] is False
    assert result["no_match_event"] == "comment_no_restaurant_intent_match"

    monkeypatch.setattr(
        workflows,
        "classify_restaurant_comment_for_promo",
        lambda *_args, **_kwargs: {"success": False, "error": "model unavailable"},
    )
    result = workflows.resolve_comment_automation_trigger(
        "What time do you open?",
        {"comment_trigger_mode": "restaurant_intent", "trigger_keywords": []},
        {"business_id": "business-1"},
        {"id": "comment-2"},
    )
    assert result["matched"] is False
    assert result["no_match_event"] == "comment_classifier_failed"


def test_rag_failure_returns_empty_fallback_context(monkeypatch):
    monkeypatch.setattr(
        workflows, "generate_query_embedding", lambda _text: (_ for _ in ()).throw(RuntimeError("down"))
    )
    result = workflows.retrieve_knowledge_context("ig-1", "When are you open?")
    assert result == {"chunks": [], "error": "down", "enabled": True}


def test_lead_helpers_normalize_phone_and_reject_phone_as_name():
    extracted = workflows.fallback_phone_from_text("Call me at (604) 555-1212")
    assert workflows.normalize_phone_number(extracted) == "+16045551212"
    assert workflows.clean_extracted_name("6045551212", "6045551212") is None
    assert workflows.clean_extracted_name("Ada Lovelace", "Ada Lovelace") == "Ada Lovelace"


@pytest.mark.parametrize(
    ("status", "expected"),
    [("expired", "expired"), ("redeemed", "already_redeemed"), ("void", "void")],
)
def test_redemption_terminal_states_are_preserved(monkeypatch, status, expected):
    code = {"id": "code-1", "code": "SAVE10", "status": status}
    monkeypatch.setattr(workflows, "is_supabase_configured", lambda: True)
    monkeypatch.setattr(workflows, "get_authenticated_api_user", lambda: {"id": "staff-1"})
    monkeypatch.setattr(workflows, "user_has_instagram_account_access", lambda *_args: {"id": "ig-1"})
    monkeypatch.setattr(workflows, "expire_expired_promo_codes", lambda: [])
    monkeypatch.setattr(workflows, "get_promo_code_by_code", lambda *_args: code)

    with create_app().test_request_context(
        "/api/promo-codes/redeem",
        method="POST",
        json={"instagram_account_id": "ig-1", "code": "save10"},
    ):
        response = workflows.redeem_promo_code_api()

    assert response.get_json()["result"] == expected


def test_sms_stop_word_closes_conversation_without_generating_reply(monkeypatch):
    closed = []
    monkeypatch.setattr(workflows, "sms_conversation_message_exists", lambda _sid: False)
    monkeypatch.setattr(
        workflows,
        "get_latest_promo_lead_by_phone",
        lambda _phone: {"id": "lead-1", "instagram_account_id": "ig-1", "contact_id": "contact-1"},
    )
    monkeypatch.setattr(
        workflows,
        "get_instagram_account_by_id",
        lambda _account_id: {"id": "ig-1", "business_id": "business-1", "status": "connected"},
    )
    monkeypatch.setattr(workflows, "get_contact_by_id", lambda _contact_id: {"id": "contact-1"})
    monkeypatch.setattr(
        workflows,
        "ensure_sms_conversation",
        lambda *_args, **_kwargs: {"id": "conversation-1", "status": "open", "extra_metadata": {}},
    )
    monkeypatch.setattr(workflows, "insert_sms_conversation_message", lambda _row: {"id": "inbound-1"})
    monkeypatch.setattr(workflows, "touch_sms_conversation", lambda _id: None)
    monkeypatch.setattr(workflows, "close_sms_conversation", lambda *args, **kwargs: closed.append((args, kwargs)))
    monkeypatch.setattr(workflows, "upsert_meta_webhook_event", lambda **_kwargs: None)
    monkeypatch.setattr(
        workflows,
        "generate_reply",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("OpenAI should not run")),
    )

    result = workflows.process_twilio_sms_reply({"MessageSid": "SM1", "From": "+16045551212", "Body": " stop "})
    assert result["processing_result"] == "twilio_sms_conversation_closed"
    assert closed[0][1]["extra_metadata"]["closed_by"] == "sms_stop_keyword"


def test_staff_api_requires_authentication(monkeypatch):
    monkeypatch.setattr(workflows, "is_supabase_configured", lambda: True)
    monkeypatch.setattr(workflows, "get_authenticated_api_user", lambda: None)
    response = create_app().test_client().get("/api/knowledge-chunks?instagram_account_id=ig-1")
    assert response.status_code == 401
    assert response.get_json()["code"] == "http_401"
    assert response.get_json()["request_id"]
