import base64
import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from flask import Flask, Response, jsonify, request
from openai_client import extract_lead_contact_info, generate_query_embedding, generate_reply
from supabase_client import (
    SupabaseError,
    claim_sms_message,
    close_sms_conversation,
    create_promotion_setup,
    create_sms_message,
    ensure_contact,
    ensure_dm_session,
    expire_expired_promo_codes,
    ensure_promo_code,
    ensure_promo_lead,
    ensure_sms_conversation,
    fetch_dm_history,
    fetch_sms_conversation_history,
    get_comment_by_instagram_id,
    get_contact_by_id,
    get_collecting_promo_lead,
    get_instagram_account,
    get_instagram_account_by_id,
    get_instagram_post,
    get_latest_promo_lead_by_phone,
    get_promo_code_by_id,
    get_promo_code_by_code,
    get_promo_lead_by_promo_code,
    get_promotion_setup,
    get_redemption_followup_sms_by_promo_code,
    has_prior_comment_automation,
    insert_dm_message,
    insert_knowledge_chunk,
    is_configured as is_supabase_configured,
    is_promo_code_valid,
    iso_from_meta_timestamp,
    list_due_sms_messages,
    list_knowledge_chunk_filter_values,
    list_knowledge_chunks,
    list_instagram_post_media_ids,
    mark_sms_message_failed,
    mark_sms_message_sent,
    match_knowledge_chunks,
    message_exists,
    insert_sms_conversation_message,
    redeem_promo_code,
    sms_conversation_message_exists,
    touch_dm_session,
    touch_sms_conversation,
    update_contact_sms_details,
    update_comment_automation,
    update_promo_lead,
    update_promotion_setup,
    upsert_instagram_post,
    upsert_comment,
    upsert_dm_session_state,
    upsert_meta_webhook_event,
    user_has_instagram_account_access,
)

try:
    from twilio.request_validator import RequestValidator
except ImportError:  # pragma: no cover - dependency is installed in deployed backend requirements.
    RequestValidator = None


app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
INSTAGRAM_SEND_MESSAGE_URL = "https://graph.instagram.com/v24.0/me/messages"
INSTAGRAM_COMMENT_REPLIES_URL = "https://graph.instagram.com/v24.0/{comment_id}/replies"
INSTAGRAM_MEDIA_URL = "https://graph.instagram.com/v24.0/{instagram_user_id}/media"
PROMOTION_POLL_INTERVAL_SECONDS = 30
PROMOTION_POLL_TIMEOUT_SECONDS = 5 * 60
SMS_STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
SMS_REPLY_TARGET_CHARS = 320
MAX_SMS_BODY_CHARS = 700
conversation_history = {}


def get_allowed_frontend_origins():
    configured_origin = os.environ.get("FRONTEND_ORIGIN")
    origins = {"http://127.0.0.1:5173", "http://localhost:5173"}
    if configured_origin:
        origins.add(configured_origin.rstrip("/"))
    return origins


@app.after_request
def add_api_cors_headers(response):
    if not request.path.startswith("/api/"):
        return response

    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") in get_allowed_frontend_origins():
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-Followup-Cron-Secret"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


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


def normalize_phone_number(raw_phone):
    raw = str(raw_phone or "").strip()
    if not raw:
        return None

    if raw.startswith("+"):
        digits = re.sub(r"\D", "", raw)
        return f"+{digits}" if 8 <= len(digits) <= 15 else None

    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return None


def enforce_sms_body_limit(text, max_chars=MAX_SMS_BODY_CHARS):
    normalized_text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(normalized_text) <= max_chars:
        return normalized_text

    suffix = "..."
    truncated = normalized_text[: max_chars - len(suffix)].rstrip()
    return f"{truncated}{suffix}"


def require_twilio_config():
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
    messaging_service_sid = os.environ.get("TWILIO_MESSAGING_SERVICE_SID", "").strip()
    if not account_sid or not auth_token or not messaging_service_sid:
        raise RuntimeError("TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_MESSAGING_SERVICE_SID must be set")
    return account_sid, auth_token, messaging_service_sid


def send_twilio_sms(to_phone_e164, body):
    try:
        account_sid, auth_token, messaging_service_sid = require_twilio_config()
    except RuntimeError as exc:
        return {"success": False, "error": str(exc)}

    safe_body = enforce_sms_body_limit(body)
    payload = urllib.parse.urlencode(
        {
            "To": to_phone_e164,
            "MessagingServiceSid": messaging_service_sid,
            "Body": safe_body,
        }
    ).encode("utf-8")
    credentials = base64.b64encode(f"{account_sid}:{auth_token}".encode("utf-8")).decode("ascii")
    api_request = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
        data=payload,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(api_request, timeout=10) as response:
            parsed_body = json.loads(response.read().decode("utf-8"))
            return {
                "success": True,
                "status_code": response.status,
                "data": parsed_body,
                "sid": parsed_body.get("sid"),
            }
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        return {"success": False, "status_code": exc.code, "error": error_body}
    except urllib.error.URLError as exc:
        return {"success": False, "error": str(exc.reason)}
    except json.JSONDecodeError as exc:
        return {"success": False, "error": f"Invalid JSON response: {exc}"}


def create_and_send_sms(
    instagram_account_id,
    contact_id,
    to_phone_e164,
    body,
    purpose,
    promo_code_id=None,
    promo_lead_id=None,
    extra_metadata=None,
):
    body = enforce_sms_body_limit(body)
    sms_message = create_sms_message(
        {
            "instagram_account_id": instagram_account_id,
            "contact_id": contact_id,
            "promo_code_id": promo_code_id,
            "promo_lead_id": promo_lead_id,
            "to_phone_e164": to_phone_e164,
            "body": body,
            "purpose": purpose,
            "status": "sending",
            "extra_metadata": extra_metadata or {},
        }
    )
    send_response = send_twilio_sms(to_phone_e164, body)
    merged_metadata = {
        **(sms_message.get("extra_metadata") or {}),
        "twilio_response": send_response,
    }
    if send_response.get("success"):
        updated_sms = mark_sms_message_sent(
            sms_message["id"],
            twilio_message_sid=send_response.get("sid"),
            extra_metadata=merged_metadata,
        )
        return updated_sms, send_response

    updated_sms = mark_sms_message_failed(
        sms_message,
        send_response.get("error") or "Twilio SMS send failed",
        extra_metadata=merged_metadata,
    )
    return updated_sms, send_response


def empty_twiml_response(status=200):
    return Response("<Response></Response>", status=status, mimetype="text/xml")


def get_public_request_url():
    url = request.url
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip()
    if forwarded_proto and "://" in url:
        return f"{forwarded_proto}://{url.split('://', 1)[1]}"
    return url


def should_validate_twilio_signature():
    return parse_bool_env("TWILIO_VALIDATE_SIGNATURE", True)


def validate_twilio_request():
    if not should_validate_twilio_signature():
        return True, None

    if RequestValidator is None:
        return False, "Twilio request validator dependency is not installed"

    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
    if not auth_token:
        return False, "TWILIO_AUTH_TOKEN is not set"

    signature = request.headers.get("X-Twilio-Signature", "")
    validator = RequestValidator(auth_token)
    is_valid = validator.validate(get_public_request_url(), request.form.to_dict(flat=True), signature)
    return is_valid, None if is_valid else "Invalid Twilio signature"


def build_sms_chat_system_prompt(system_prompt):
    sms_instruction = (
        "You are replying by SMS. Keep replies concise, natural, and helpful. "
        f"Do not use markdown. Target {SMS_REPLY_TARGET_CHARS} characters or fewer. "
        f"Never exceed {MAX_SMS_BODY_CHARS} characters."
    )
    if system_prompt:
        return f"{system_prompt}\n\n{sms_instruction}"
    return sms_instruction


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


def build_lead_capture_dm_text():
    return "Thanks! Reply with your name and phone number and we'll text you the promo code."


def build_missing_lead_fields_reply(has_name, has_phone):
    if not has_name and not has_phone:
        return "Please reply with your name and phone number so we can text you the promo code."
    if not has_name:
        return "Thanks, I have your phone number. What name should we put with the promo code?"
    return "Thanks, I have your name. What phone number should we text the promo code to?"


def build_code_sms_body(promo_code):
    return f"Your promo code is {promo_code['code']}. Show this code when you redeem your offer."


def build_sms_followup_body(promo_code):
    return f"Thanks for visiting and using code {promo_code['code']}! How was your experience?"


def parse_db_timestamp(value):
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            timestamp = value
        else:
            timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid database timestamp: %s", value)
        return None
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp


def is_post_automation_active(post, now=None):
    now = now or datetime.now(timezone.utc)
    starts_at = parse_db_timestamp(post.get("automation_starts_at"))
    ends_at = parse_db_timestamp(post.get("automation_ends_at"))
    if starts_at and now < starts_at:
        return False
    if ends_at and now >= ends_at:
        return False
    return True


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


def api_error(message, status=400, **extra):
    return jsonify({"error": message, **extra}), status


def get_api_bearer_token():
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ").strip()
    return token or None


def verify_supabase_user_token(token):
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    anon_key = os.environ.get("SUPABASE_ANON_KEY")
    if not supabase_url or not anon_key:
        raise SupabaseError("SUPABASE_URL and SUPABASE_ANON_KEY must be set for API auth")

    api_request = urllib.request.Request(
        f"{supabase_url}/auth/v1/user",
        headers={
            "apikey": anon_key,
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(api_request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        logger.warning("Supabase auth verification failed: %s %s", exc.code, error_body)
        return None
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise SupabaseError(f"Supabase auth verification failed: {exc}") from exc


def get_authenticated_api_user():
    token = get_api_bearer_token()
    if not token:
        return None
    return verify_supabase_user_token(token)


def parse_api_timestamp(value, field_name):
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def validate_promotion_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")

    instagram_account_id = str(payload.get("instagram_account_id") or "").strip()
    if not instagram_account_id:
        raise ValueError("instagram_account_id is required")

    raw_keywords = payload.get("trigger_keywords")
    if not isinstance(raw_keywords, list):
        raise ValueError("trigger_keywords must be an array")

    trigger_keywords = []
    for keyword in raw_keywords:
        if not isinstance(keyword, str):
            continue
        stripped = keyword.strip()
        if stripped and stripped not in trigger_keywords:
            trigger_keywords.append(stripped)
    if not trigger_keywords:
        raise ValueError("At least one trigger keyword is required")

    comment_reply_text = str(payload.get("comment_reply_text") or "").strip()
    if not comment_reply_text:
        raise ValueError("comment_reply_text is required")

    automation_starts_at = parse_api_timestamp(payload.get("automation_starts_at"), "automation_starts_at")
    automation_ends_at = parse_api_timestamp(payload.get("automation_ends_at"), "automation_ends_at")
    if automation_starts_at and automation_ends_at:
        starts_at = datetime.fromisoformat(automation_starts_at)
        ends_at = datetime.fromisoformat(automation_ends_at)
        if ends_at <= starts_at:
            raise ValueError("automation_ends_at must be after automation_starts_at")

    duration = payload.get("promo_code_valid_duration_hours")
    if duration in (None, ""):
        promo_code_valid_duration_hours = None
    else:
        try:
            promo_code_valid_duration_hours = int(duration)
        except (TypeError, ValueError) as exc:
            raise ValueError("promo_code_valid_duration_hours must be a positive integer") from exc
        if promo_code_valid_duration_hours <= 0:
            raise ValueError("promo_code_valid_duration_hours must be a positive integer")

    return {
        "instagram_account_id": instagram_account_id,
        "trigger_keywords": trigger_keywords,
        "automation_starts_at": automation_starts_at,
        "automation_ends_at": automation_ends_at,
        "promo_code_valid_duration_hours": promo_code_valid_duration_hours,
        "comment_reply_text": comment_reply_text,
        "dm_prompt": str(payload.get("dm_prompt") or "").strip() or None,
        "code_prefix": str(payload.get("code_prefix") or "").strip() or None,
    }


def fetch_instagram_media(instagram_user_id):
    access_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN")
    if not access_token:
        raise RuntimeError("INSTAGRAM_ACCESS_TOKEN is not set")

    query = urllib.parse.urlencode(
        {
            "fields": "id,caption,media_type,media_url,permalink,timestamp",
            "limit": "25",
        }
    )
    api_request = urllib.request.Request(
        f"{INSTAGRAM_MEDIA_URL.format(instagram_user_id=instagram_user_id)}?{query}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(api_request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Instagram media fetch failed: {exc.code} {error_body}") from exc
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Instagram media fetch failed: {exc}") from exc

    media = payload.get("data")
    return media if isinstance(media, list) else []


def fetch_instagram_media_item(media_id):
    access_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN")
    if not access_token:
        raise RuntimeError("INSTAGRAM_ACCESS_TOKEN is not set")

    query = urllib.parse.urlencode(
        {
            "fields": "id,caption,media_type,media_url,permalink,timestamp",
        }
    )
    api_request = urllib.request.Request(
        f"https://graph.instagram.com/v24.0/{media_id}?{query}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(api_request, timeout=10) as response:
            media_item = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Instagram media item fetch failed: {exc.code} {error_body}") from exc
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Instagram media item fetch failed: {exc}") from exc

    if not isinstance(media_item, dict) or not media_item.get("id"):
        raise RuntimeError("Instagram media item fetch returned no media ID")
    return media_item


def parse_meta_media_timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def newest_unseen_media(media_items, baseline_media_ids):
    baseline = set(baseline_media_ids or [])
    candidates = [
        item
        for item in media_items
        if isinstance(item, dict) and item.get("id") and item["id"] not in baseline
    ]
    candidates.sort(
        key=lambda item: parse_meta_media_timestamp(item.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return candidates[0] if candidates else None


def build_promotional_post_row(setup, media_item):
    timestamp = parse_meta_media_timestamp(media_item.get("timestamp"))
    promotion_metadata = {}
    if setup.get("code_prefix"):
        promotion_metadata["code_prefix"] = setup["code_prefix"]

    return {
        "instagram_account_id": setup["instagram_account_id"],
        "instagram_media_id": media_item["id"],
        "caption": media_item.get("caption"),
        "media_type": media_item.get("media_type"),
        "media_url": media_item.get("media_url"),
        "permalink": media_item.get("permalink"),
        "posted_at": timestamp.isoformat() if timestamp else None,
        "post_type": "promotional",
        "automation_enabled": True,
        "automation_starts_at": setup.get("automation_starts_at"),
        "automation_ends_at": setup.get("automation_ends_at"),
        "trigger_keywords": setup.get("trigger_keywords") or [],
        "comment_reply_text": setup.get("comment_reply_text") or "Sent you a DM!",
        "dm_prompt": setup.get("dm_prompt"),
        "promo_code_valid_duration_hours": setup.get("promo_code_valid_duration_hours"),
        "promotion_metadata": promotion_metadata,
        "extra_metadata": {
            "promotion_setup_id": setup["id"],
            "source": "promotion_setup_poll",
            "meta_media": media_item,
        },
    }


def build_regular_post_row(instagram_account_id, media_item, source):
    timestamp = parse_meta_media_timestamp(media_item.get("timestamp"))
    return {
        "instagram_account_id": instagram_account_id,
        "instagram_media_id": media_item["id"],
        "caption": media_item.get("caption"),
        "media_type": media_item.get("media_type"),
        "media_url": media_item.get("media_url"),
        "permalink": media_item.get("permalink"),
        "posted_at": timestamp.isoformat() if timestamp else None,
        "post_type": "regular",
        "automation_enabled": False,
        "extra_metadata": {
            "source": source,
            "meta_media": media_item,
        },
    }


def sync_unknown_regular_media_before_promotion(account):
    existing_media_ids = set(list_instagram_post_media_ids(account["id"]))
    media_items = fetch_instagram_media(account["instagram_user_id"])
    upserted_posts = []

    for media_item in media_items:
        if not isinstance(media_item, dict) or not media_item.get("id"):
            continue
        if media_item["id"] in existing_media_ids:
            continue

        post = upsert_instagram_post(
            build_regular_post_row(
                account["id"],
                media_item,
                source="promotion_setup_pre_sync",
            )
        )
        if post:
            upserted_posts.append(post)
        existing_media_ids.add(media_item["id"])

    return {
        "upserted_count": len(upserted_posts),
        "upserted_media_ids": [post["instagram_media_id"] for post in upserted_posts if post.get("instagram_media_id")],
    }


def run_promotion_setup_poll(setup_id):
    try:
        setup = get_promotion_setup(setup_id)
        if not setup or setup.get("status") not in {"pending", "polling"}:
            return

        account = get_instagram_account_by_id(setup["instagram_account_id"])
        if not account:
            update_promotion_setup(setup_id, {"status": "error", "error_message": "Instagram account not found"})
            return

        started_at = datetime.now(timezone.utc)
        expires_at = started_at + timedelta(seconds=PROMOTION_POLL_TIMEOUT_SECONDS)
        setup = update_promotion_setup(
            setup_id,
            {
                "status": "polling",
                "poll_started_at": started_at.isoformat(),
                "poll_expires_at": expires_at.isoformat(),
            },
        ) or setup

        baseline_media_ids = setup.get("baseline_media_ids") or []
        while datetime.now(timezone.utc) <= expires_at:
            update_promotion_setup(setup_id, {"last_polled_at": datetime.now(timezone.utc).isoformat()})
            media_item = newest_unseen_media(fetch_instagram_media(account["instagram_user_id"]), baseline_media_ids)

            if media_item:
                post = upsert_instagram_post(build_promotional_post_row(setup, media_item))
                update_promotion_setup(
                    setup_id,
                    {
                        "status": "found",
                        "post_id": post["id"] if post else None,
                        "found_instagram_media_id": media_item["id"],
                        "found_caption": media_item.get("caption"),
                        "found_at": datetime.now(timezone.utc).isoformat(),
                        "extra_metadata": {
                            **(setup.get("extra_metadata") or {}),
                            "found_media": media_item,
                        },
                    },
                )
                return

            time.sleep(PROMOTION_POLL_INTERVAL_SECONDS)

        update_promotion_setup(
            setup_id,
            {
                "status": "expired",
                "error_message": "No new Instagram media found within the 5 minute polling window",
            },
        )
    except Exception as exc:
        logger.exception("Promotion setup polling failed for setup %s", setup_id)
        try:
            update_promotion_setup(setup_id, {"status": "error", "error_message": str(exc)})
        except Exception:
            logger.exception("Failed to mark promotion setup %s as errored", setup_id)


def start_promotion_setup_poll(setup_id):
    thread = threading.Thread(target=run_promotion_setup_poll, args=(setup_id,), daemon=True)
    thread.start()


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


def clean_extracted_text(value):
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned or cleaned.lower() in {"null", "none", "unknown", "n/a"}:
        return None
    return cleaned


def looks_like_phone_text(text):
    return bool(fallback_phone_from_text(text) or normalize_phone_number(text))


def clean_extracted_name(value, message_text):
    cleaned = clean_extracted_text(value)
    if not cleaned:
        return None

    normalized_name = re.sub(r"\s+", " ", cleaned).strip()
    if not normalized_name or looks_like_phone_text(normalized_name):
        return None

    # Avoid storing a whole phone-only message as the customer's name when the
    # extractor guesses from sparse context.
    message = str(message_text or "").strip()
    if message and normalize_phone_number(message) and normalized_name.casefold() == message.casefold():
        return None

    return normalized_name


def fallback_phone_from_text(text):
    match = re.search(r"(\+?\d[\d\s().-]{7,}\d)", text or "")
    return match.group(1).strip() if match else None


def handle_promo_lead_capture(instagram_account, contact, session, history, message_text, inbound_message, event_type, payload):
    lead = get_collecting_promo_lead(contact["id"])
    if not lead:
        return None

    extraction = extract_lead_contact_info(history)
    parsed_phone_from_message = fallback_phone_from_text(message_text)
    extracted_phone = parsed_phone_from_message or clean_extracted_text(extraction.get("phone"))
    extracted_name = clean_extracted_name(extraction.get("customer_name"), message_text)

    existing_name = clean_extracted_text(lead.get("customer_name"))
    existing_phone_raw = clean_extracted_text(lead.get("phone_raw"))
    existing_phone_e164 = clean_extracted_text(lead.get("phone_e164"))

    phone_raw = extracted_phone or existing_phone_raw
    normalized_phone = normalize_phone_number(phone_raw)
    phone_e164 = normalized_phone or existing_phone_e164
    if not existing_name and not extracted_name and not phone_e164 and not looks_like_phone_text(message_text):
        extracted_name = clean_extracted_name(message_text, message_text)

    customer_name = existing_name or extracted_name
    has_name = bool(customer_name)
    has_phone = bool(phone_e164)
    now = datetime.now(timezone.utc).isoformat()
    lead_metadata = lead.get("extra_metadata") or {}
    if not isinstance(lead_metadata, dict):
        lead_metadata = {}
    lead_metadata = {
        **lead_metadata,
        "last_extraction": {
            **extraction,
            "deterministic_phone": parsed_phone_from_message,
            "normalized_phone": phone_e164,
        },
    }

    lead_patch = {
        "customer_name": customer_name,
        "phone_raw": phone_raw,
        "phone_e164": phone_e164,
        "extra_metadata": lead_metadata,
    }

    sms_message = None
    sms_response = None
    if has_name and has_phone:
        lead_patch["sms_consent_at"] = lead.get("sms_consent_at") or now
        lead_patch["status"] = "ready"
        update_contact_sms_details(
            contact["id"],
            customer_name=customer_name,
            phone_raw=phone_raw,
            phone_e164=phone_e164,
            sms_consent_at=lead_patch["sms_consent_at"],
        )
        lead = update_promo_lead(lead["id"], lead_patch) or lead
        promo_code = get_promo_code_by_id(lead["promo_code_id"])
        sms_message, sms_response = create_and_send_sms(
            instagram_account["id"],
            contact["id"],
            phone_e164,
            build_code_sms_body(promo_code),
            purpose="promo_code",
            promo_code_id=promo_code["id"],
            promo_lead_id=lead["id"],
            extra_metadata={"source": "instagram_lead_capture"},
        )
        if sms_response.get("success"):
            lead = update_promo_lead(
                lead["id"],
                {
                    "status": "code_sms_sent",
                    "error_message": None,
                    "extra_metadata": {
                        **(lead.get("extra_metadata") or {}),
                        "last_sms_message_id": sms_message["id"] if sms_message else None,
                    },
                },
            ) or lead
            reply_text = "Perfect - I just texted your promo code to that number."
            processing_result = "promo_lead_code_sms_sent"
            processing_status = "processed"
            error_message = None
        else:
            error_message = sms_response.get("error") or "Twilio SMS send failed"
            lead = update_promo_lead(
                lead["id"],
                {
                    "status": "collecting",
                    "error_message": error_message,
                    "extra_metadata": {
                        **(lead.get("extra_metadata") or {}),
                        "last_sms_message_id": sms_message["id"] if sms_message else None,
                        "last_sms_failure": error_message,
                    },
                },
            ) or lead
            reply_text = "I could not send the text just now. Please double-check your phone number and send it again."
            processing_result = "promo_lead_code_sms_failed"
            processing_status = "failed"
    else:
        lead = update_promo_lead(lead["id"], lead_patch) or lead
        reply_text = build_missing_lead_fields_reply(has_name, has_phone)
        processing_result = "promo_lead_collecting"
        processing_status = "processed"
        error_message = None

    send_message_response = send_instagram_dm(contact["instagram_user_id"], reply_text)
    sent_message_id = get_sent_instagram_message_id(send_message_response)
    delivery_status = "sent" if send_message_response["success"] else "failed"
    assistant_message = insert_dm_message(
        session_id=session["id"],
        contact_id=contact["id"],
        role="assistant",
        direction="outbound",
        content=reply_text,
        instagram_message_id=sent_message_id,
        delivery_status=delivery_status,
        error_message=None if send_message_response["success"] else send_message_response.get("error"),
    )
    upsert_dm_session_state(
        session["id"],
        summary=f"Promo lead capture inbound: {message_text}\nReply: {reply_text}",
    )
    touch_dm_session(session["id"])
    upsert_meta_webhook_event(
        event_id=(inbound_message.get("instagram_message_id") if inbound_message else None) or (inbound_message.get("id") if inbound_message else None),
        business_id=instagram_account["business_id"],
        instagram_account_id=instagram_account["id"],
        event_type=event_type,
        payload=payload,
        processing_status=processing_status if send_message_response["success"] else "failed",
        error_message=error_message or (None if send_message_response["success"] else send_message_response.get("error")),
    )

    return {
        "processing_result": processing_result if send_message_response["success"] else "promo_lead_dm_send_failed",
        "openai_result": None,
        "send_message_response": send_message_response,
        "db_result": {
            "business_id": instagram_account["business_id"],
            "instagram_account_id": instagram_account["id"],
            "contact_id": contact["id"],
            "session_id": session["id"],
            "inbound_message_id": inbound_message["id"] if inbound_message else None,
            "assistant_message_id": assistant_message["id"] if assistant_message else None,
            "promo_lead_id": lead["id"],
            "sms_message_id": sms_message["id"] if sms_message else None,
        },
    }


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
    lead_capture_result = handle_promo_lead_capture(
        instagram_account,
        contact,
        session,
        history,
        message_text,
        inbound_message,
        event_type,
        payload,
    )
    if lead_capture_result:
        return lead_capture_result

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

    discovered_unknown_post = False
    post = get_instagram_post(instagram_account["id"], media_id)
    if not post:
        discovered_post = None
        try:
            media_item = fetch_instagram_media_item(media_id)
            discovered_post = upsert_instagram_post(
                build_regular_post_row(
                    instagram_account["id"],
                    media_item,
                    source="comment_webhook_media_discovery",
                )
            )
        except RuntimeError as exc:
            logger.warning("Unable to discover Instagram media %s from comment webhook: %s", media_id, exc)

        if discovered_post:
            post = discovered_post
            discovered_unknown_post = True
        else:
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
        processing_result = log_comment_event(
            "comment_unknown_media_discovered" if discovered_unknown_post else "comment_automation_not_applicable",
            processing_status="processed" if discovered_unknown_post else "ignored",
        )
        return {
            "processing_result": processing_result,
            "openai_result": None,
            "public_reply_response": None,
            "private_reply_response": None,
            "db_result": {
                "instagram_account_id": instagram_account["id"],
                "post_id": post["id"],
                "comment_id": comment["id"] if comment else None,
                "discovered_post": discovered_unknown_post,
                "instagram_media_id": media_id,
            },
        }

    if not is_post_automation_active(post):
        comment = upsert_comment(**base_comment_kwargs)
        processing_result = log_comment_event("comment_automation_window_inactive")
        return {
            "processing_result": processing_result,
            "openai_result": None,
            "public_reply_response": None,
            "private_reply_response": None,
            "db_result": {
                "instagram_account_id": instagram_account["id"],
                "post_id": post["id"],
                "comment_id": comment["id"] if comment else None,
                "automation_starts_at": post.get("automation_starts_at"),
                "automation_ends_at": post.get("automation_ends_at"),
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
        valid_duration_hours=post.get("promo_code_valid_duration_hours"),
    )
    promo_lead = ensure_promo_lead(
        instagram_account["id"],
        post["id"],
        contact["id"],
        comment["id"],
        promo_code["id"],
        extra_metadata={
            "matched_keyword": matched_keyword,
            "source": "comment_to_dm",
        },
    )
    session = ensure_dm_session(instagram_account["id"], contact["id"])
    touch_dm_session(session["id"])

    public_reply_response = reply_to_instagram_comment(
        comment_id,
        post.get("comment_reply_text") or "Sent you a DM!",
    )
    public_reply_id = get_sent_instagram_message_id(public_reply_response)
    public_error = None if public_reply_response["success"] else public_reply_response.get("error")

    private_reply_text = build_lead_capture_dm_text()
    private_reply_response = send_instagram_private_reply(comment_id, private_reply_text)
    private_reply_id = get_sent_instagram_message_id(private_reply_response)
    private_error = None if private_reply_response["success"] else private_reply_response.get("error")
    delivery_status = "sent" if private_reply_response["success"] else "failed"
    automation_errors = [error for error in [public_error, private_error] if error]
    automation_error = "\n".join(automation_errors) if automation_errors else None

    assistant_message = insert_dm_message(
        session_id=session["id"],
        contact_id=contact["id"],
        role="assistant",
        direction="outbound",
        content=private_reply_text,
        instagram_message_id=private_reply_id,
        delivery_status=delivery_status,
        error_message=automation_error,
    )

    if private_error:
        automation_status = "private_reply_failed"
        processing_result = "comment_private_reply_failed"
        processing_status = "failed"
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
        summary=f"Last comment: {comment_text}\nLast private reply: {private_reply_text}",
    )
    touch_dm_session(session["id"])
    log_comment_event(processing_result, processing_status, automation_error)

    return {
        "processing_result": processing_result,
        "openai_result": None,
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
            "promo_lead_id": promo_lead["id"],
            "promo_code_valid_from": promo_code.get("valid_from"),
            "promo_code_expires_at": promo_code.get("expires_at"),
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


def send_instagram_tagged_dm(recipient_id, text, tag):
    body = {
        "recipient": {"id": recipient_id},
        "message": {"text": text},
    }
    if tag:
        body["tag"] = tag
    return send_instagram_api_request(INSTAGRAM_SEND_MESSAGE_URL, body)


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


def process_twilio_sms_reply(form_payload):
    message_sid = form_payload.get("MessageSid") or form_payload.get("SmsMessageSid") or form_payload.get("SmsSid")
    from_phone = normalize_phone_number(form_payload.get("From"))
    body = str(form_payload.get("Body") or "").strip()

    if not message_sid or not from_phone or not body:
        return {"processing_result": "twilio_sms_missing_required_fields"}

    if sms_conversation_message_exists(message_sid):
        return {"processing_result": "twilio_sms_duplicate_ignored", "twilio_message_sid": message_sid}

    lead = get_latest_promo_lead_by_phone(from_phone)
    if not lead:
        return {
            "processing_result": "twilio_sms_unknown_phone",
            "twilio_message_sid": message_sid,
            "from_phone": from_phone,
        }

    instagram_account = get_instagram_account_by_id(lead["instagram_account_id"])
    if not instagram_account or instagram_account.get("status") != "connected":
        return {
            "processing_result": "twilio_sms_instagram_account_not_connected",
            "twilio_message_sid": message_sid,
            "instagram_account_id": lead["instagram_account_id"],
        }

    contact = get_contact_by_id(lead["contact_id"])
    conversation = ensure_sms_conversation(
        instagram_account["id"],
        lead["contact_id"],
        from_phone,
        promo_lead_id=lead["id"],
        extra_metadata={
            "source": "twilio_sms_webhook",
            "last_inbound_to": form_payload.get("To"),
            "messaging_service_sid": form_payload.get("MessagingServiceSid"),
        },
    )
    inbound_message = insert_sms_conversation_message(
        {
            "conversation_id": conversation["id"],
            "instagram_account_id": instagram_account["id"],
            "contact_id": lead["contact_id"],
            "promo_lead_id": lead["id"],
            "role": "user",
            "direction": "inbound",
            "body": body,
            "twilio_message_sid": message_sid,
            "delivery_status": "received",
            "raw_payload": form_payload,
        }
    )
    touch_sms_conversation(conversation["id"])

    normalized_body = body.strip().upper()
    if normalized_body in SMS_STOP_WORDS:
        existing_metadata = conversation.get("extra_metadata") or {}
        if not isinstance(existing_metadata, dict):
            existing_metadata = {}
        close_sms_conversation(
            conversation["id"],
            extra_metadata={
                **existing_metadata,
                "closed_by": "sms_stop_keyword",
                "closed_message_sid": message_sid,
            },
        )
        upsert_meta_webhook_event(
            event_id=f"twilio:sms:{message_sid}",
            business_id=instagram_account["business_id"],
            instagram_account_id=instagram_account["id"],
            event_type="twilio-sms",
            payload=form_payload,
            processing_status="processed",
        )
        return {
            "processing_result": "twilio_sms_conversation_closed",
            "conversation_id": conversation["id"],
            "inbound_message_id": inbound_message["id"] if inbound_message else None,
        }

    if conversation.get("status") == "closed":
        return {
            "processing_result": "twilio_sms_conversation_already_closed",
            "conversation_id": conversation["id"],
            "inbound_message_id": inbound_message["id"] if inbound_message else None,
        }

    history = fetch_sms_conversation_history(conversation["id"])
    rag_result = retrieve_knowledge_context(instagram_account["id"], body)
    started_at = time.perf_counter()
    openai_result = generate_reply(
        history,
        system_prompt=build_sms_chat_system_prompt(instagram_account.get("system_prompt")),
        knowledge_context=rag_result["chunks"],
    )
    reply_text = enforce_sms_body_limit(openai_result["reply_text"])
    latency_ms = int((time.perf_counter() - started_at) * 1000)
    sms_message, send_response = create_and_send_sms(
        instagram_account["id"],
        lead["contact_id"],
        from_phone,
        reply_text,
        purpose="sms_llm_reply",
        promo_lead_id=lead["id"],
        extra_metadata={
            "source": "twilio_sms_llm_reply",
            "conversation_id": conversation["id"],
            "inbound_twilio_message_sid": message_sid,
            "openai": {
                "success": openai_result.get("success"),
                "used_fallback": openai_result.get("used_fallback"),
                "model": openai_result.get("model"),
                "response_id": openai_result.get("response_id"),
                "error": openai_result.get("error"),
            },
            "rag": {
                "enabled": rag_result["enabled"],
                "match_count": len(rag_result["chunks"]),
                "error": rag_result["error"],
            },
        },
    )
    delivery_status = "sent" if send_response.get("success") else "failed"
    error_message = None if send_response.get("success") else send_response.get("error")
    outbound_message = insert_sms_conversation_message(
        {
            "conversation_id": conversation["id"],
            "instagram_account_id": instagram_account["id"],
            "contact_id": lead["contact_id"],
            "promo_lead_id": lead["id"],
            "role": "assistant",
            "direction": "outbound",
            "body": reply_text,
            "twilio_message_sid": send_response.get("sid"),
            "delivery_status": delivery_status,
            "model": openai_result.get("model"),
            "response_id": openai_result.get("response_id"),
            "token_usage": openai_result.get("token_usage") or {},
            "latency_ms": latency_ms,
            "error_message": error_message or openai_result.get("error"),
            "raw_payload": {
                "send_response": send_response,
                "sms_message_id": sms_message["id"] if sms_message else None,
            },
        }
    )
    touch_sms_conversation(conversation["id"])
    upsert_meta_webhook_event(
        event_id=f"twilio:sms:{message_sid}",
        business_id=instagram_account["business_id"],
        instagram_account_id=instagram_account["id"],
        event_type="twilio-sms",
        payload=form_payload,
        processing_status="processed" if send_response.get("success") else "failed",
        error_message=error_message,
    )

    return {
        "processing_result": "twilio_sms_replied" if send_response.get("success") else "twilio_sms_send_failed",
        "openai_result": openai_result,
        "send_response": send_response,
        "db_result": {
            "business_id": instagram_account["business_id"],
            "instagram_account_id": instagram_account["id"],
            "contact_id": lead["contact_id"],
            "conversation_id": conversation["id"],
            "inbound_message_id": inbound_message["id"] if inbound_message else None,
            "outbound_message_id": outbound_message["id"] if outbound_message else None,
            "sms_message_id": sms_message["id"] if sms_message else None,
            "rag": {
                "enabled": rag_result["enabled"],
                "match_count": len(rag_result["chunks"]),
                "error": rag_result["error"],
            },
            "contact": contact,
        },
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


@app.post("/api/twilio/sms-webhook")
def twilio_sms_webhook():
    is_valid, validation_error = validate_twilio_request()
    if not is_valid:
        logger.warning("Twilio SMS webhook validation failed: %s", validation_error)
        return Response("Forbidden", status=403, mimetype="text/plain")

    if not is_supabase_configured():
        logger.warning("Supabase is not configured; inbound Twilio SMS ignored.")
        return empty_twiml_response()

    form_payload = request.form.to_dict(flat=True)
    try:
        result = process_twilio_sms_reply(form_payload)
        logger.info("Twilio SMS webhook processed: %s", result.get("processing_result"))
        return empty_twiml_response()
    except SupabaseError as exc:
        logger.exception("Failed to process inbound Twilio SMS")
        return Response(f"Database error: {exc}", status=500, mimetype="text/plain")


@app.route("/api/promotions", methods=["OPTIONS"])
@app.route("/api/promotions/<setup_id>", methods=["OPTIONS"])
@app.route("/api/promo-codes/redeem", methods=["OPTIONS"])
@app.route("/api/knowledge-chunks", methods=["OPTIONS"])
@app.route("/api/followups/process-due", methods=["OPTIONS"])
def promotion_api_options(setup_id=None):
    return "", 204


def serialize_knowledge_chunk_for_api(chunk):
    if not chunk:
        return None
    return {
        "id": chunk.get("id"),
        "instagram_account_id": chunk.get("instagram_account_id"),
        "text": chunk.get("text"),
        "type": chunk.get("type"),
        "source_url": chunk.get("source_url"),
        "page_path": chunk.get("page_path"),
        "title": chunk.get("title"),
        "meta_description": chunk.get("meta_description"),
        "extra_metadata": chunk.get("extra_metadata") or {},
        "content_hash": chunk.get("content_hash"),
        "created_at": chunk.get("created_at"),
    }


def clean_optional_string(value):
    if value in (None, ""):
        return None
    cleaned = str(value).strip()
    return cleaned or None


def validate_knowledge_chunk_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")

    instagram_account_id = str(payload.get("instagram_account_id") or "").strip()
    if not instagram_account_id:
        raise ValueError("instagram_account_id is required")

    text = str(payload.get("text") or "").strip()
    if not text:
        raise ValueError("text is required")

    return {
        "instagram_account_id": instagram_account_id,
        "text": text,
        "title": clean_optional_string(payload.get("title")),
        "type": clean_optional_string(payload.get("type")),
        "source_url": clean_optional_string(payload.get("source_url")),
        "page_path": clean_optional_string(payload.get("page_path")),
        "category": clean_optional_string(payload.get("category")),
    }


def parse_positive_int_arg(name, default, maximum=None):
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than 0")
    if maximum is not None:
        return min(value, maximum)
    return value


@app.get("/api/knowledge-chunks")
def get_knowledge_chunks_api():
    if not is_supabase_configured():
        return api_error("Supabase is not configured", 500)

    try:
        user = get_authenticated_api_user()
    except SupabaseError as exc:
        return api_error(str(exc), 500)

    if not user or not user.get("id"):
        return api_error("Unauthorized", 401)

    instagram_account_id = str(request.args.get("instagram_account_id") or "").strip()
    if not instagram_account_id:
        return api_error("instagram_account_id is required", 400)

    try:
        page = parse_positive_int_arg("page", 1)
        page_size = parse_positive_int_arg("page_size", 10, maximum=50)
    except ValueError as exc:
        return api_error(str(exc), 400)

    chunk_type = clean_optional_string(request.args.get("type"))
    category = clean_optional_string(request.args.get("category"))

    try:
        account = user_has_instagram_account_access(user["id"], instagram_account_id)
    except SupabaseError as exc:
        return api_error(str(exc), 500)
    if not account:
        return api_error("You do not have access to this Instagram account", 403)

    try:
        rows = list_knowledge_chunks(
            account["id"],
            limit=page_size + 1,
            offset=(page - 1) * page_size,
            chunk_type=chunk_type,
            category=category,
        )
        filter_values = list_knowledge_chunk_filter_values(account["id"])
    except SupabaseError as exc:
        return api_error(str(exc), 500)

    has_more = len(rows) > page_size
    chunks = rows[:page_size]
    return jsonify(
        {
            "chunks": [serialize_knowledge_chunk_for_api(chunk) for chunk in chunks],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "has_more": has_more,
            },
            "filters": filter_values,
        }
    )


@app.post("/api/knowledge-chunks")
def create_knowledge_chunk_api():
    if not is_supabase_configured():
        return api_error("Supabase is not configured", 500)

    try:
        user = get_authenticated_api_user()
    except SupabaseError as exc:
        return api_error(str(exc), 500)

    if not user or not user.get("id"):
        return api_error("Unauthorized", 401)

    try:
        chunk_input = validate_knowledge_chunk_payload(request.get_json(silent=True))
    except ValueError as exc:
        return api_error(str(exc), 400)

    try:
        account = user_has_instagram_account_access(user["id"], chunk_input["instagram_account_id"])
    except SupabaseError as exc:
        return api_error(str(exc), 500)
    if not account:
        return api_error("You do not have access to this Instagram account", 403)

    try:
        embedding = generate_query_embedding(chunk_input["text"])
    except Exception as exc:  # noqa: BLE001 - return a usable API error for embedding failures.
        logger.exception("Failed to embed knowledge chunk")
        return api_error(f"Failed to generate embedding: {exc}", 502)

    row = {
        "instagram_account_id": account["id"],
        "text": chunk_input["text"],
        "type": chunk_input["type"],
        "source_url": chunk_input["source_url"],
        "page_path": chunk_input["page_path"],
        "title": chunk_input["title"],
        "extra_metadata": {
            "source": "frontend-login",
            "created_by": user["id"],
            **({"category": chunk_input["category"]} if chunk_input["category"] else {}),
        },
        "content_hash": hashlib.sha256(chunk_input["text"].encode("utf-8")).hexdigest(),
        "embedding": embedding,
    }

    try:
        chunk = insert_knowledge_chunk(row)
    except SupabaseError as exc:
        return api_error(str(exc), 500)

    return jsonify({"chunk": serialize_knowledge_chunk_for_api(chunk)}), 201


def serialize_promo_code_for_api(code_row):
    if not code_row:
        return None
    return {
        "id": code_row.get("id"),
        "code": code_row.get("code"),
        "status": code_row.get("status"),
        "valid_from": code_row.get("valid_from"),
        "expires_at": code_row.get("expires_at"),
        "redeemed_at": code_row.get("redeemed_at"),
    }


def serialize_sms_message_for_api(sms_message):
    if not sms_message:
        return None
    return {
        "id": sms_message.get("id"),
        "promo_code_id": sms_message.get("promo_code_id"),
        "purpose": sms_message.get("purpose"),
        "status": sms_message.get("status"),
        "scheduled_for": sms_message.get("scheduled_for"),
        "sent_at": sms_message.get("sent_at"),
        "twilio_message_sid": sms_message.get("twilio_message_sid"),
        "error_message": sms_message.get("error_message"),
    }


def parse_iso_datetime(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def schedule_sms_redemption_followup(promo_code):
    existing = get_redemption_followup_sms_by_promo_code(promo_code["id"])
    if existing:
        return existing

    lead = get_promo_lead_by_promo_code(promo_code["id"])
    contact = get_contact_by_id(promo_code["contact_id"])
    phone_e164 = (lead or {}).get("phone_e164") or (contact or {}).get("phone_e164")
    if not phone_e164:
        return None

    redeemed_at = parse_iso_datetime(promo_code.get("redeemed_at")) or datetime.now(timezone.utc)
    delay_minutes = parse_int_env("FOLLOWUP_DELAY_MINUTES", 10)
    scheduled_for = redeemed_at + timedelta(minutes=delay_minutes)
    sms_row = {
        "instagram_account_id": promo_code["instagram_account_id"],
        "contact_id": promo_code["contact_id"],
        "promo_code_id": promo_code["id"],
        "promo_lead_id": (lead or {}).get("id"),
        "to_phone_e164": phone_e164,
        "body": enforce_sms_body_limit(build_sms_followup_body(promo_code)),
        "purpose": "post_redemption_followup",
        "status": "pending",
        "scheduled_for": scheduled_for.isoformat(),
        "extra_metadata": {
            "source": "promo_code_redemption",
            "followup_delay_minutes": delay_minutes,
        },
    }
    try:
        return create_sms_message(sms_row)
    except SupabaseError as exc:
        if "ig_sms_messages_one_redemption_followup_per_code_idx" not in str(exc):
            raise
        return get_redemption_followup_sms_by_promo_code(promo_code["id"])


def validate_redeem_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")

    instagram_account_id = str(payload.get("instagram_account_id") or "").strip()
    if not instagram_account_id:
        raise ValueError("instagram_account_id is required")

    code = str(payload.get("code") or "").strip().upper()
    if not code:
        raise ValueError("code is required")

    return {
        "instagram_account_id": instagram_account_id,
        "code": code,
    }


def get_followup_cron_secret():
    return os.environ.get("FOLLOWUP_CRON_SECRET", "").strip()


def is_followup_cron_authorized():
    expected_secret = get_followup_cron_secret()
    provided_secret = request.headers.get("X-Followup-Cron-Secret", "").strip()
    return bool(expected_secret and provided_secret and provided_secret == expected_secret)


def process_due_sms_message(sms_message):
    claimed = claim_sms_message(sms_message["id"])
    if not claimed:
        return {
            "id": sms_message["id"],
            "status": "skipped",
            "reason": "not_pending",
        }

    extra_metadata = claimed.get("extra_metadata") or {}
    if not isinstance(extra_metadata, dict):
        extra_metadata = {}

    try:
        body = enforce_sms_body_limit(claimed["body"])
        send_response = send_twilio_sms(claimed["to_phone_e164"], body)
        if not send_response.get("success"):
            error_message = send_response.get("error") or "Twilio SMS send failed"
            failed = mark_sms_message_failed(
                claimed,
                error_message,
                extra_metadata={
                    **extra_metadata,
                    "send_response": send_response,
                },
            )
            return {
                "id": claimed["id"],
                "status": "failed",
                "error": error_message,
                "sms_message": serialize_sms_message_for_api(failed),
            }

        sent = mark_sms_message_sent(
            claimed["id"],
            twilio_message_sid=send_response.get("sid"),
            extra_metadata={
                **extra_metadata,
                "send_response": send_response,
            },
        )
        return {
            "id": claimed["id"],
            "status": "sent",
            "sms_message": serialize_sms_message_for_api(sent),
        }
    except (RuntimeError, SupabaseError) as exc:
        failed = mark_sms_message_failed(
            claimed,
            str(exc),
            extra_metadata={
                **extra_metadata,
                "processor_error": str(exc),
            },
        )
        return {
            "id": claimed["id"],
            "status": "failed",
            "error": str(exc),
            "sms_message": serialize_sms_message_for_api(failed),
        }


@app.post("/api/followups/process-due")
def process_due_followups_api():
    if not is_followup_cron_authorized():
        return api_error("Unauthorized", 401)
    if not is_supabase_configured():
        return api_error("Supabase is not configured", 500)

    batch_size = parse_int_env("FOLLOWUP_BATCH_SIZE", 20)
    try:
        due_sms_messages = list_due_sms_messages(limit=batch_size)
    except SupabaseError as exc:
        return api_error(str(exc), 500)

    results = [process_due_sms_message(sms_message) for sms_message in due_sms_messages]
    return jsonify(
        {
            "processed": len(results),
            "results": results,
        }
    )


@app.post("/api/promo-codes/redeem")
def redeem_promo_code_api():
    if not is_supabase_configured():
        return api_error("Supabase is not configured", 500)

    try:
        user = get_authenticated_api_user()
    except SupabaseError as exc:
        return api_error(str(exc), 500)

    if not user or not user.get("id"):
        return api_error("Unauthorized", 401)

    try:
        redeem_input = validate_redeem_payload(request.get_json(silent=True))
    except ValueError as exc:
        return api_error(str(exc), 400)

    try:
        account = user_has_instagram_account_access(user["id"], redeem_input["instagram_account_id"])
    except SupabaseError as exc:
        return api_error(str(exc), 500)
    if not account:
        return api_error("You do not have access to this Instagram account", 403)

    try:
        expire_expired_promo_codes()
        code_row = get_promo_code_by_code(account["id"], redeem_input["code"])
    except SupabaseError as exc:
        return api_error(str(exc), 500)

    if not code_row:
        return jsonify({"result": "not_found", "promo_code": None})

    if code_row.get("status") == "issued" and not is_promo_code_valid(code_row):
        try:
            expire_expired_promo_codes()
            code_row = get_promo_code_by_code(account["id"], redeem_input["code"]) or code_row
        except SupabaseError as exc:
            return api_error(str(exc), 500)

    status = code_row.get("status")
    if status == "expired":
        return jsonify({"result": "expired", "promo_code": serialize_promo_code_for_api(code_row)})
    if status == "redeemed":
        return jsonify({"result": "already_redeemed", "promo_code": serialize_promo_code_for_api(code_row)})
    if status == "void":
        return jsonify({"result": "void", "promo_code": serialize_promo_code_for_api(code_row)})
    if status != "issued":
        return jsonify({"result": "not_found", "promo_code": None})

    try:
        redeemed_code = redeem_promo_code(code_row["id"], redeemed_by=user["id"])
        if not redeemed_code:
            refreshed_code = get_promo_code_by_code(account["id"], redeem_input["code"]) or code_row
            result = "already_redeemed" if refreshed_code.get("status") == "redeemed" else refreshed_code.get("status")
            return jsonify({"result": result, "promo_code": serialize_promo_code_for_api(refreshed_code)})
        followup = schedule_sms_redemption_followup(redeemed_code)
    except SupabaseError as exc:
        return api_error(str(exc), 500)
    except ValueError as exc:
        return api_error(str(exc), 500)

    return jsonify(
        {
            "result": "redeemed",
            "promo_code": serialize_promo_code_for_api(redeemed_code),
            "followup": serialize_sms_message_for_api(followup),
        }
    )


@app.post("/api/promotions")
def create_promotion():
    if not is_supabase_configured():
        return api_error("Supabase is not configured", 500)

    try:
        user = get_authenticated_api_user()
    except SupabaseError as exc:
        return api_error(str(exc), 500)

    if not user or not user.get("id"):
        return api_error("Unauthorized", 401)

    try:
        promotion_input = validate_promotion_payload(request.get_json(silent=True))
    except ValueError as exc:
        return api_error(str(exc), 400)

    try:
        account = user_has_instagram_account_access(user["id"], promotion_input["instagram_account_id"])
    except SupabaseError as exc:
        return api_error(str(exc), 500)
    if not account:
        return api_error("You do not have access to this Instagram account", 403)

    try:
        pre_sync_result = sync_unknown_regular_media_before_promotion(account)
        baseline_media_ids = list_instagram_post_media_ids(account["id"])
    except SupabaseError as exc:
        return api_error(str(exc), 500)
    except RuntimeError as exc:
        return api_error(str(exc), 502)

    now = datetime.now(timezone.utc)
    setup_row = {
        "instagram_account_id": account["id"],
        "submitted_by": user["id"],
        "trigger_keywords": promotion_input["trigger_keywords"],
        "automation_starts_at": promotion_input["automation_starts_at"],
        "automation_ends_at": promotion_input["automation_ends_at"],
        "promo_code_valid_duration_hours": promotion_input["promo_code_valid_duration_hours"],
        "comment_reply_text": promotion_input["comment_reply_text"],
        "dm_prompt": promotion_input["dm_prompt"],
        "code_prefix": promotion_input["code_prefix"],
        "baseline_media_ids": baseline_media_ids,
        "status": "pending",
        "poll_expires_at": (now + timedelta(seconds=PROMOTION_POLL_TIMEOUT_SECONDS)).isoformat(),
        "extra_metadata": {
            "baseline_count": len(baseline_media_ids),
            "submitted_from": "frontend-login",
            "pre_sync": pre_sync_result,
        },
    }

    try:
        setup = create_promotion_setup(setup_row)
    except SupabaseError as exc:
        error_text = str(exc)
        if "ig_promotion_setups_one_active_per_account_idx" in error_text or "duplicate key" in error_text:
            return api_error("An active promotion setup is already polling for this Instagram account", 409)
        return api_error(error_text, 500)

    start_promotion_setup_poll(setup["id"])
    return jsonify({"setup": setup}), 202


@app.get("/api/promotions/<setup_id>")
def get_promotion(setup_id):
    if not is_supabase_configured():
        return api_error("Supabase is not configured", 500)

    try:
        user = get_authenticated_api_user()
    except SupabaseError as exc:
        return api_error(str(exc), 500)

    if not user or not user.get("id"):
        return api_error("Unauthorized", 401)

    try:
        setup = get_promotion_setup(setup_id)
    except SupabaseError as exc:
        return api_error(str(exc), 500)
    if not setup:
        return api_error("Promotion setup not found", 404)

    try:
        has_access = user_has_instagram_account_access(user["id"], setup["instagram_account_id"])
    except SupabaseError as exc:
        return api_error(str(exc), 500)
    if not has_access:
        return api_error("You do not have access to this promotion setup", 403)

    return jsonify({"setup": setup})


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
    expired_promo_codes = []

    if is_supabase_configured() and event_type in {"dm-related", "comment-related"}:
        try:
            expired_promo_codes = expire_expired_promo_codes()
        except SupabaseError:
            logger.exception("Failed to expire stale promo codes")

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

    if expired_promo_codes:
        log_entry["expired_promo_codes"] = {
            "count": len(expired_promo_codes),
            "codes": expired_promo_codes,
        }

    logger.info("Webhook received:\n%s", json.dumps(log_entry, indent=2, default=str))

    return "OK", 200, {"Content-Type": "text/plain; charset=utf-8"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
