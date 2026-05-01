import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from openai_client import generate_query_embedding, generate_reply
from supabase_client import (
    SupabaseError,
    ensure_contact,
    ensure_dm_session,
    ensure_promo_code,
    fetch_dm_history,
    get_comment_by_instagram_id,
    get_instagram_account,
    get_instagram_post,
    has_prior_comment_automation,
    insert_dm_message,
    is_configured as is_supabase_configured,
    iso_from_meta_timestamp,
    match_knowledge_chunks,
    message_exists,
    touch_dm_session,
    update_comment_automation,
    upsert_comment,
    upsert_dm_session_state,
    upsert_meta_webhook_event,
)


app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
INSTAGRAM_SEND_MESSAGE_URL = "https://graph.instagram.com/v24.0/me/messages"
INSTAGRAM_COMMENT_REPLIES_URL = "https://graph.instagram.com/v24.0/{comment_id}/replies"
conversation_history = {}


def parse_bool_env(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def parse_int_env(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


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


def get_comment_processing_info(payload):
    if not isinstance(payload, dict):
        return {"should_process": False, "reason": "invalid_payload"}

    entries = payload.get("entry")
    if not isinstance(entries, list) or not entries:
        return {"should_process": False, "reason": "missing_entry"}

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        account_id = entry.get("id")
        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue

        for change in changes:
            if not isinstance(change, dict):
                continue

            value = change.get("value")
            if not isinstance(value, dict):
                continue

            comment_text = value.get("text") or value.get("message")
            comment_id = value.get("id") or value.get("comment_id")
            media = value.get("media") if isinstance(value.get("media"), dict) else {}
            media_id = media.get("id") or value.get("media_id")
            commenter = value.get("from") if isinstance(value.get("from"), dict) else {}
            commenter_id = commenter.get("id") or value.get("user_id")
            commenter_username = commenter.get("username") or value.get("username")

            if not comment_id or not media_id or not comment_text:
                continue

            if commenter_id and commenter_id == account_id:
                return {"should_process": False, "reason": "skipped_self_comment"}

            return {
                "should_process": True,
                "reason": "inbound_comment",
                "account_id": account_id,
                "comment_id": comment_id,
                "media_id": media_id,
                "commenter_id": commenter_id,
                "commenter_username": commenter_username,
                "comment_text": comment_text,
                "parent_comment_id": value.get("parent_id") or value.get("parent_comment_id"),
                "timestamp": value.get("created_time") or entry.get("time"),
                "raw_value": value,
            }

    return {"should_process": False, "reason": "no_comment_found"}


def find_matched_keyword(comment_text, keywords):
    if not isinstance(comment_text, str) or not isinstance(keywords, list):
        return None

    normalized_text = comment_text.casefold()
    for keyword in keywords:
        if not isinstance(keyword, str):
            continue

        normalized_keyword = keyword.strip().casefold()
        if normalized_keyword and normalized_keyword in normalized_text:
            return keyword.strip()

    return None


def build_comment_dm_input(post, comment_info, matched_keyword, promo_code=None):
    campaign_prompt = post.get("dm_prompt") or ""
    caption = post.get("caption") or ""
    lines = [
        "Generate a concise Instagram DM private reply for a user who commented on a promotional post.",
    ]
    if promo_code:
        lines.append(f"Include this exact promo code in the DM and do not alter it: {promo_code}")
    lines.extend(
        [
            "",
            f"Post caption: {caption}",
            f"Campaign instructions: {campaign_prompt}",
            f"Matched keyword: {matched_keyword}",
            f"Comment text: {comment_info['comment_text']}",
        ]
    )
    if promo_code:
        lines.append(f"Promo code: {promo_code}")
    content = "\n".join(lines)
    return [{"role": "user", "content": content}]


def ensure_reply_contains_promo_code(reply_text, promo_code):
    if not promo_code or promo_code in (reply_text or ""):
        return reply_text

    reply_text = (reply_text or "").strip()
    suffix = f"Your code is {promo_code}."
    if not reply_text:
        return suffix
    return f"{reply_text}\n\n{suffix}"


def retrieve_knowledge_context(instagram_account_id, query_text):
    if not parse_bool_env("RAG_ENABLED", True):
        return {"chunks": [], "error": None, "enabled": False}

    if not instagram_account_id or not query_text:
        return {"chunks": [], "error": None, "enabled": True}

    try:
        query_embedding = generate_query_embedding(query_text)
        chunks = match_knowledge_chunks(
            instagram_account_id,
            query_embedding,
            match_count=parse_int_env("RAG_MATCH_COUNT", 5),
        )
        return {"chunks": chunks, "error": None, "enabled": True}
    except Exception as exc:  # noqa: BLE001 - retrieval should not block replies.
        logger.warning("RAG retrieval failed for Instagram account %s: %s", instagram_account_id, exc)
        return {"chunks": [], "error": str(exc), "enabled": True}


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

    history = fetch_dm_history(session["id"])
    rag_result = retrieve_knowledge_context(instagram_account["id"], message_text)
    started_at = time.perf_counter()
    openai_result = generate_reply(
        history,
        system_prompt=instagram_account.get("system_prompt"),
        knowledge_context=rag_result["chunks"],
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
            "rag": {
                "enabled": rag_result["enabled"],
                "match_count": len(rag_result["chunks"]),
                "error": rag_result["error"],
            },
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


def process_comment_with_database(comment_info, payload, event_type):
    account_id = comment_info["account_id"]
    comment_id = comment_info["comment_id"]
    media_id = comment_info["media_id"]
    commenter_id = comment_info.get("commenter_id")
    comment_text = comment_info["comment_text"]
    created_at = iso_from_meta_timestamp(comment_info.get("timestamp"))

    instagram_account = get_instagram_account(account_id)
    if not instagram_account:
        return {
            "processing_result": "instagram_account_not_configured",
            "openai_result": None,
            "public_reply_response": None,
            "private_reply_response": None,
            "db_result": {"account_id": account_id, "media_id": media_id, "comment_id": comment_id},
        }

    def log_comment_event(processing_result, processing_status="ignored", error_message=None):
        upsert_meta_webhook_event(
            event_id=comment_id,
            business_id=instagram_account["business_id"],
            instagram_account_id=instagram_account["id"],
            event_type=event_type,
            payload=payload,
            processing_status=processing_status,
            error_message=error_message,
        )
        return processing_result

    if instagram_account.get("status") != "connected":
        processing_result = log_comment_event("instagram_account_not_connected")
        return {
            "processing_result": processing_result,
            "openai_result": None,
            "public_reply_response": None,
            "private_reply_response": None,
            "db_result": {
                "instagram_account_id": instagram_account["id"],
                "account_status": instagram_account.get("status"),
            },
        }

    if get_comment_by_instagram_id(instagram_account["id"], comment_id):
        processing_result = log_comment_event("duplicate_comment_ignored")
        return {
            "processing_result": processing_result,
            "openai_result": None,
            "public_reply_response": None,
            "private_reply_response": None,
            "db_result": {
                "instagram_account_id": instagram_account["id"],
                "instagram_comment_id": comment_id,
            },
        }

    post = get_instagram_post(instagram_account["id"], media_id)
    if not post:
        processing_result = log_comment_event("instagram_post_not_configured")
        return {
            "processing_result": processing_result,
            "openai_result": None,
            "public_reply_response": None,
            "private_reply_response": None,
            "db_result": {
                "instagram_account_id": instagram_account["id"],
                "instagram_media_id": media_id,
                "instagram_comment_id": comment_id,
            },
        }

    if not commenter_id:
        comment = upsert_comment(
            instagram_account_id=instagram_account["id"],
            post_id=post["id"],
            instagram_comment_id=comment_id,
            text=comment_text,
            created_at_ig=created_at,
            automation_status="error",
            extra_metadata={"webhook_value": comment_info.get("raw_value") or {}},
        )
        error_message = "Comment webhook did not include commenter ID"
        update_comment_automation(comment["id"], "error", automation_error=error_message)
        processing_result = log_comment_event("comment_missing_commenter_id", "failed", error_message)
        return {
            "processing_result": processing_result,
            "openai_result": None,
            "public_reply_response": None,
            "private_reply_response": None,
            "db_result": {
                "instagram_account_id": instagram_account["id"],
                "post_id": post["id"],
                "comment_id": comment["id"] if comment else None,
            },
        }

    contact = ensure_contact(
        instagram_account["id"],
        commenter_id,
        username=comment_info.get("commenter_username"),
    )
    base_comment_kwargs = {
        "instagram_account_id": instagram_account["id"],
        "post_id": post["id"],
        "instagram_comment_id": comment_id,
        "text": comment_text,
        "contact_id": contact["id"],
        "created_at_ig": created_at,
        "extra_metadata": {"webhook_value": comment_info.get("raw_value") or {}},
    }

    if post.get("post_type") != "promotional" or not post.get("automation_enabled"):
        comment = upsert_comment(**base_comment_kwargs)
        processing_result = log_comment_event("comment_automation_not_applicable")
        return {
            "processing_result": processing_result,
            "openai_result": None,
            "public_reply_response": None,
            "private_reply_response": None,
            "db_result": {
                "instagram_account_id": instagram_account["id"],
                "post_id": post["id"],
                "comment_id": comment["id"] if comment else None,
            },
        }

    matched_keyword = find_matched_keyword(comment_text, post.get("trigger_keywords"))
    if not matched_keyword:
        comment = upsert_comment(**base_comment_kwargs)
        processing_result = log_comment_event("comment_no_keyword_match")
        return {
            "processing_result": processing_result,
            "openai_result": None,
            "public_reply_response": None,
            "private_reply_response": None,
            "db_result": {
                "instagram_account_id": instagram_account["id"],
                "post_id": post["id"],
                "comment_id": comment["id"] if comment else None,
            },
        }

    if has_prior_comment_automation(post["id"], contact["id"]):
        comment = upsert_comment(
            **base_comment_kwargs,
            automation_status="duplicate",
            matched_keyword=matched_keyword,
        )
        processing_result = log_comment_event("comment_duplicate_automation")
        return {
            "processing_result": processing_result,
            "openai_result": None,
            "public_reply_response": None,
            "private_reply_response": None,
            "db_result": {
                "instagram_account_id": instagram_account["id"],
                "post_id": post["id"],
                "comment_id": comment["id"] if comment else None,
                "contact_id": contact["id"],
            },
        }

    comment = upsert_comment(
        **base_comment_kwargs,
        automation_status="pending",
        matched_keyword=matched_keyword,
    )
    promotion_metadata = post.get("promotion_metadata") or {}
    code_prefix = promotion_metadata.get("code_prefix") if isinstance(promotion_metadata, dict) else None
    promo_code = ensure_promo_code(
        instagram_account["id"],
        post["id"],
        contact["id"],
        comment["id"],
        prefix=code_prefix,
    )
    session = ensure_dm_session(instagram_account["id"], contact["id"])
    touch_dm_session(session["id"])

    public_reply_response = reply_to_instagram_comment(
        comment_id,
        post.get("comment_reply_text") or "Sent you a DM!",
    )
    public_reply_id = get_sent_instagram_message_id(public_reply_response)
    public_error = None if public_reply_response["success"] else public_reply_response.get("error")

    rag_query = "\n".join(
        part
        for part in [
            post.get("dm_prompt") or "",
            matched_keyword or "",
            comment_text,
        ]
        if part
    )
    rag_result = retrieve_knowledge_context(instagram_account["id"], rag_query)
    started_at = time.perf_counter()
    openai_result = generate_reply(
        build_comment_dm_input(post, comment_info, matched_keyword, promo_code=promo_code["code"]),
        system_prompt=instagram_account.get("system_prompt"),
        knowledge_context=rag_result["chunks"],
    )
    openai_result["reply_text"] = ensure_reply_contains_promo_code(
        openai_result["reply_text"],
        promo_code["code"],
    )
    latency_ms = int((time.perf_counter() - started_at) * 1000)
    openai_error = openai_result.get("error") if openai_result["used_fallback"] else None

    private_reply_response = send_instagram_private_reply(comment_id, openai_result["reply_text"])
    private_reply_id = get_sent_instagram_message_id(private_reply_response)
    private_error = None if private_reply_response["success"] else private_reply_response.get("error")
    delivery_status = "sent" if private_reply_response["success"] else "failed"
    automation_errors = [error for error in [public_error, openai_error, private_error] if error]
    automation_error = "\n".join(automation_errors) if automation_errors else None

    assistant_message = insert_dm_message(
        session_id=session["id"],
        contact_id=contact["id"],
        role="assistant",
        direction="outbound",
        content=openai_result["reply_text"],
        instagram_message_id=private_reply_id,
        delivery_status=delivery_status,
        model=openai_result.get("model"),
        token_usage=openai_result.get("token_usage"),
        latency_ms=latency_ms,
        error_message=automation_error,
    )

    if private_error:
        automation_status = "private_reply_failed"
        processing_result = "comment_private_reply_failed"
        processing_status = "failed"
    elif openai_error:
        automation_status = "openai_failed"
        processing_result = "comment_openai_failed"
        processing_status = "processed"
    elif public_error:
        automation_status = "comment_reply_failed"
        processing_result = "comment_public_reply_failed"
        processing_status = "processed"
    else:
        automation_status = "sent"
        processing_result = "comment_automation_sent"
        processing_status = "processed"

    update_comment_automation(
        comment["id"],
        automation_status,
        public_reply_comment_id=public_reply_id,
        private_reply_message_id=private_reply_id,
        automation_error=automation_error,
    )
    upsert_dm_session_state(
        session["id"],
        last_response_id=openai_result.get("response_id"),
        summary=f"Last comment: {comment_text}\nLast private reply: {openai_result['reply_text']}",
    )
    touch_dm_session(session["id"])
    log_comment_event(processing_result, processing_status, automation_error)

    return {
        "processing_result": processing_result,
        "openai_result": openai_result,
        "public_reply_response": public_reply_response,
        "private_reply_response": private_reply_response,
        "db_result": {
            "business_id": instagram_account["business_id"],
            "instagram_account_id": instagram_account["id"],
            "post_id": post["id"],
            "comment_id": comment["id"] if comment else None,
            "contact_id": contact["id"],
            "session_id": session["id"],
            "assistant_message_id": assistant_message["id"] if assistant_message else None,
            "promo_code_id": promo_code["id"],
            "promo_code": promo_code["code"],
            "rag": {
                "enabled": rag_result["enabled"],
                "match_count": len(rag_result["chunks"]),
                "error": rag_result["error"],
            },
        },
    }


def send_instagram_api_request(url, body):
    access_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN")

    if not access_token:
        return {
            "success": False,
            "error": "INSTAGRAM_ACCESS_TOKEN is not set",
        }

    encoded_body = json.dumps(body).encode("utf-8")
    api_request = urllib.request.Request(
        url,
        data=encoded_body,
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


def send_instagram_dm(recipient_id, text):
    return send_instagram_api_request(
        INSTAGRAM_SEND_MESSAGE_URL,
        {
            "recipient": {"id": recipient_id},
            "message": {"text": text},
        },
    )


def send_instagram_private_reply(comment_id, text):
    return send_instagram_api_request(
        INSTAGRAM_SEND_MESSAGE_URL,
        {
            "recipient": {"comment_id": comment_id},
            "message": {"text": text},
        },
    )


def reply_to_instagram_comment(comment_id, text):
    return send_instagram_api_request(
        INSTAGRAM_COMMENT_REPLIES_URL.format(comment_id=comment_id),
        {"message": text},
    )


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
    public_reply_response = None
    private_reply_response = None
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
    elif event_type == "comment-related":
        comment_info = get_comment_processing_info(payload)
        processing_result = comment_info["reason"]

        if comment_info["should_process"]:
            try:
                if is_supabase_configured():
                    comment_result = process_comment_with_database(comment_info, payload, event_type)
                    processing_result = comment_result["processing_result"]
                    openai_result = comment_result["openai_result"]
                    public_reply_response = comment_result["public_reply_response"]
                    private_reply_response = comment_result["private_reply_response"]
                    db_result = comment_result["db_result"]
                else:
                    logger.warning("Supabase is not configured; comment automation is disabled.")
                    processing_result = "comment_automation_requires_database"
            except SupabaseError as exc:
                logger.exception("Failed to process inbound Instagram comment")
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
            "knowledge_context_count": openai_result.get("knowledge_context_count"),
        }

    if send_message_response is not None:
        log_entry["send_message_response"] = send_message_response

    if public_reply_response is not None:
        log_entry["public_reply_response"] = public_reply_response

    if private_reply_response is not None:
        log_entry["private_reply_response"] = private_reply_response

    if db_result is not None:
        log_entry["db_result"] = db_result

    logger.info("Webhook received:\n%s", json.dumps(log_entry, indent=2, default=str))

    return "OK", 200, {"Content-Type": "text/plain; charset=utf-8"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
