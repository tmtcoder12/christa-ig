import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from openai_client import generate_reply
from supabase_client import (
    SupabaseError,
    ensure_contact,
    ensure_dm_session,
    fetch_dm_history,
    get_business,
    get_instagram_account,
    insert_dm_message,
    is_configured as is_supabase_configured,
    iso_from_meta_timestamp,
    message_exists,
    touch_dm_session,
    upsert_dm_session_state,
    upsert_meta_webhook_event,
)


app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
INSTAGRAM_SEND_MESSAGE_URL = "https://graph.instagram.com/v24.0/me/messages"
conversation_history = {}


def classify_meta_event(payload):
    if not isinstance(payload, dict):
        return "unknown"

    entries = payload.get("entry")
    if not isinstance(entries, list):
        return "unknown"

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        if entry.get("changes"):
            return "comment-related"

        messaging = entry.get("messaging")
        if isinstance(messaging, list) and messaging:
            return "dm-related"

    return "unknown"


def get_dm_processing_info(payload):
    if not isinstance(payload, dict):
        return {"should_reply": False, "reason": "invalid_payload"}

    entries = payload.get("entry")
    if not isinstance(entries, list) or not entries:
        return {"should_reply": False, "reason": "missing_entry"}

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        account_id = entry.get("id")
        messaging_items = entry.get("messaging")
        if not isinstance(messaging_items, list):
            continue

        for item in messaging_items:
            if not isinstance(item, dict):
                continue

            sender = item.get("sender")
            message = item.get("message")
            if item.get("read") is not None:
                return {"should_reply": False, "reason": "skipped_read_receipt"}

            if not isinstance(sender, dict):
                continue

            sender_id = sender.get("id")
            if sender_id == account_id:
                return {"should_reply": False, "reason": "skipped_echo"}

            if not isinstance(message, dict):
                continue

            if message.get("is_echo"):
                return {"should_reply": False, "reason": "skipped_echo"}

            message_text = message.get("text")
            if not sender_id or not message_text:
                continue

            return {
                "should_reply": True,
                "reason": "inbound_text_dm",
                "account_id": account_id,
                "sender_id": sender_id,
                "message_text": message_text,
                "message_id": message.get("mid"),
                "timestamp": item.get("timestamp") or entry.get("time"),
            }

    return {"should_reply": False, "reason": "no_inbound_dm_sender_found"}


def get_user_history(sender_id):
    return conversation_history.setdefault(sender_id, [])


def append_user_message(sender_id, text):
    history = get_user_history(sender_id)
    history.append({"role": "user", "content": text})


def append_assistant_message(sender_id, text):
    history = get_user_history(sender_id)
    history.append({"role": "assistant", "content": text})


def get_sent_instagram_message_id(send_message_response):
    data = send_message_response.get("data")
    if not isinstance(data, dict):
        return None

    return data.get("message_id") or data.get("id")


def process_dm_with_database(dm_info, payload, event_type):
    account_id = dm_info["account_id"]
    sender_id = dm_info["sender_id"]
    message_text = dm_info["message_text"]
    inbound_message_id = dm_info.get("message_id")
    created_at = iso_from_meta_timestamp(dm_info.get("timestamp"))

    instagram_account = get_instagram_account(account_id)
    if not instagram_account:
        return {
            "processing_result": "instagram_account_not_configured",
            "openai_result": None,
            "send_message_response": None,
            "db_result": {
                "account_id": account_id,
                "sender_id": sender_id,
            },
        }

    if instagram_account.get("status") != "connected":
        return {
            "processing_result": "instagram_account_not_connected",
            "openai_result": None,
            "send_message_response": None,
            "db_result": {
                "instagram_account_id": instagram_account["id"],
                "account_status": instagram_account.get("status"),
            },
        }

    if inbound_message_id and message_exists(inbound_message_id):
        return {
            "processing_result": "duplicate_dm_ignored",
            "openai_result": None,
            "send_message_response": None,
            "db_result": {
                "instagram_account_id": instagram_account["id"],
                "instagram_message_id": inbound_message_id,
            },
        }

    contact = ensure_contact(instagram_account["id"], sender_id)
    session = ensure_dm_session(instagram_account["id"], contact["id"])
    inbound_message = insert_dm_message(
        session_id=session["id"],
        contact_id=contact["id"],
        role="user",
        direction="inbound",
        content=message_text,
        instagram_message_id=inbound_message_id,
        delivery_status="received",
        created_at=created_at,
    )
    touch_dm_session(session["id"])

    business = get_business(instagram_account["business_id"])
    history = fetch_dm_history(session["id"])
    started_at = time.perf_counter()
    openai_result = generate_reply(
        history,
        system_prompt=(business or {}).get("system_prompt"),
    )
    latency_ms = int((time.perf_counter() - started_at) * 1000)
    send_message_response = send_instagram_dm(sender_id, openai_result["reply_text"])
    sent_message_id = get_sent_instagram_message_id(send_message_response)

    if send_message_response["success"]:
        delivery_status = "sent"
        error_message = None
        processing_result = "fallback_sent" if openai_result["used_fallback"] else "replied"
    else:
        delivery_status = "failed"
        error_message = send_message_response.get("error")
        processing_result = "openai_failed_no_send" if openai_result["used_fallback"] else "send_failed"

    assistant_message = insert_dm_message(
        session_id=session["id"],
        contact_id=contact["id"],
        role="assistant",
        direction="outbound",
        content=openai_result["reply_text"],
        instagram_message_id=sent_message_id,
        delivery_status=delivery_status,
        model=openai_result.get("model"),
        token_usage=openai_result.get("token_usage"),
        latency_ms=latency_ms,
        error_message=error_message or openai_result.get("error"),
    )
    upsert_dm_session_state(
        session["id"],
        last_response_id=openai_result.get("response_id"),
        summary=f"Last inbound: {message_text}\nLast assistant: {openai_result['reply_text']}",
    )
    touch_dm_session(session["id"])
    upsert_meta_webhook_event(
        event_id=inbound_message_id,
        business_id=instagram_account["business_id"],
        instagram_account_id=instagram_account["id"],
        event_type=event_type,
        payload=payload,
        processing_status="processed" if send_message_response["success"] else "failed",
        error_message=error_message,
    )

    return {
        "processing_result": processing_result,
        "openai_result": openai_result,
        "send_message_response": send_message_response,
        "db_result": {
            "business_id": instagram_account["business_id"],
            "instagram_account_id": instagram_account["id"],
            "contact_id": contact["id"],
            "session_id": session["id"],
            "inbound_message_id": inbound_message["id"] if inbound_message else None,
            "assistant_message_id": assistant_message["id"] if assistant_message else None,
        },
    }


def process_dm_in_memory(dm_info):
    sender_id = dm_info["sender_id"]
    message_text = dm_info["message_text"]
    append_user_message(sender_id, message_text)
    openai_result = generate_reply(get_user_history(sender_id))
    send_message_response = send_instagram_dm(sender_id, openai_result["reply_text"])

    if send_message_response["success"]:
        append_assistant_message(sender_id, openai_result["reply_text"])
        processing_result = "fallback_sent" if openai_result["used_fallback"] else "replied"
    else:
        processing_result = "openai_failed_no_send" if openai_result["used_fallback"] else "send_failed"

    return {
        "processing_result": processing_result,
        "openai_result": openai_result,
        "send_message_response": send_message_response,
        "db_result": None,
    }


def send_instagram_dm(recipient_id, text):
    access_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN")

    if not access_token:
        return {
            "success": False,
            "error": "INSTAGRAM_ACCESS_TOKEN is not set",
        }

    body = json.dumps(
        {
            "recipient": {"id": recipient_id},
            "message": {"text": text},
        }
    ).encode("utf-8")
    api_request = urllib.request.Request(
        INSTAGRAM_SEND_MESSAGE_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(api_request, timeout=10) as response:
            body = response.read().decode("utf-8")
            parsed_body = json.loads(body)
            return {
                "success": True,
                "status_code": response.status,
                "data": parsed_body,
            }
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        return {
            "success": False,
            "status_code": exc.code,
            "error": error_body,
        }
    except urllib.error.URLError as exc:
        return {
            "success": False,
            "error": str(exc.reason),
        }
    except json.JSONDecodeError as exc:
        return {
            "success": False,
            "error": f"Invalid JSON response: {exc}",
        }


@app.get("/")
def healthcheck():
    return jsonify({"status": "ok"})


@app.get("/webhook")
def verify_webhook():
    mode = request.args.get("hub.mode")
    verify_token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge", "")
    expected_token = os.environ.get("META_VERIFY_TOKEN")

    if mode == "subscribe" and expected_token and verify_token == expected_token:
        logger.info("Meta webhook verification succeeded for path %s", request.path)
        return challenge, 200, {"Content-Type": "text/plain; charset=utf-8"}

    logger.warning(
        "Meta webhook verification failed: mode=%s token_provided=%s expected_token_set=%s",
        mode,
        bool(verify_token),
        bool(expected_token),
    )
    return "Forbidden", 403


@app.post("/webhook")
def webhook():
    payload = request.get_json(silent=True)
    body_text = request.get_data(as_text=True)
    event_type = classify_meta_event(payload)
    processing_result = "ignored"
    openai_result = None
    send_message_response = None
    db_result = None

    if event_type == "dm-related":
        dm_info = get_dm_processing_info(payload)
        processing_result = dm_info["reason"]

        if dm_info["should_reply"]:
            try:
                if is_supabase_configured():
                    dm_result = process_dm_with_database(dm_info, payload, event_type)
                else:
                    logger.warning("Supabase is not configured; using in-memory DM history.")
                    dm_result = process_dm_in_memory(dm_info)

                processing_result = dm_result["processing_result"]
                openai_result = dm_result["openai_result"]
                send_message_response = dm_result["send_message_response"]
                db_result = dm_result["db_result"]
            except SupabaseError as exc:
                logger.exception("Failed to persist inbound Instagram DM")
                processing_result = "db_error"
                db_result = {"error": str(exc)}

    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": request.method,
        "path": request.path,
        "event_type": event_type,
        "processing_result": processing_result,
        "headers": dict(request.headers),
        "query_params": request.args.to_dict(flat=False),
        "json": payload,
        "raw_body": body_text,
    }

    if openai_result is not None:
        log_entry["openai_result"] = {
            "success": openai_result["success"],
            "used_fallback": openai_result["used_fallback"],
            "error": openai_result["error"],
            "reply_text": openai_result["reply_text"],
            "model": openai_result.get("model"),
            "response_id": openai_result.get("response_id"),
        }

    if send_message_response is not None:
        log_entry["send_message_response"] = send_message_response

    if db_result is not None:
        log_entry["db_result"] = db_result

    logger.info("Webhook received:\n%s", json.dumps(log_entry, indent=2, default=str))

    return "OK", 200, {"Content-Type": "text/plain; charset=utf-8"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
