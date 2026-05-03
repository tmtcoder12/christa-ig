import json
import os
import re
import secrets
import string
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone


DEFAULT_TIMEOUT_SECONDS = 10
PROMO_CODE_ALPHABET = string.ascii_uppercase + string.digits
PROMO_CODE_SUFFIX_LENGTH = 6
PROMO_CODE_MAX_ATTEMPTS = 8
PROMOTION_SETUP_SELECT = (
    "id,instagram_account_id,submitted_by,trigger_keywords,automation_starts_at,"
    "automation_ends_at,promo_code_valid_duration_hours,comment_reply_text,dm_prompt,"
    "code_prefix,baseline_media_ids,status,post_id,found_instagram_media_id,"
    "found_caption,error_message,poll_started_at,poll_expires_at,last_polled_at,"
    "found_at,extra_metadata,created_at,updated_at"
)


class SupabaseError(Exception):
    pass


def is_configured():
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY"))


def iso_from_meta_timestamp(timestamp_ms):
    if not timestamp_ms:
        return None

    try:
        timestamp = int(timestamp_ms)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _get_config():
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url or not service_role_key:
        raise SupabaseError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")

    return supabase_url, service_role_key


def _request(method, path, params=None, payload=None, prefer=None):
    supabase_url, service_role_key = _get_config()
    query = urllib.parse.urlencode(params or {})
    url = f"{supabase_url}/rest/v1/{path}"
    if query:
        url = f"{url}?{query}"

    headers = {
        "apikey": service_role_key,
        "Authorization": f"Bearer {service_role_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer

    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    api_request = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(api_request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            response_body = response.read().decode("utf-8")
            if not response_body:
                return None
            return json.loads(response_body)
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise SupabaseError(f"Supabase {method} {path} failed: {exc.code} {error_body}") from exc
    except urllib.error.URLError as exc:
        raise SupabaseError(f"Supabase {method} {path} failed: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise SupabaseError(f"Supabase {method} {path} returned invalid JSON: {exc}") from exc


def _fetch_one(table, params):
    rows = _request("GET", table, params={**params, "limit": "1"})
    if not rows:
        return None
    return rows[0]


def _insert(table, row):
    rows = _request("POST", table, payload=row, prefer="return=representation")
    if not rows:
        return None
    return rows[0]


def _upsert(table, row, on_conflict):
    rows = _request(
        "POST",
        table,
        params={"on_conflict": on_conflict},
        payload=row,
        prefer="resolution=merge-duplicates,return=representation",
    )
    if not rows:
        return None
    return rows[0]


def _patch(table, filters, patch):
    return _request("PATCH", table, params=filters, payload=patch, prefer="return=minimal")


def _patch_returning(table, filters, patch):
    return _request("PATCH", table, params=filters, payload=patch, prefer="return=representation") or []


def _rpc(function_name, payload):
    return _request("POST", f"rpc/{function_name}", payload=payload)


def get_instagram_account(instagram_user_id):
    return _fetch_one(
        "instagram_accounts",
        {
            "instagram_user_id": f"eq.{instagram_user_id}",
            "select": "id,business_id,instagram_user_id,username,status,system_prompt",
        },
    )


def get_instagram_account_by_id(instagram_account_id):
    return _fetch_one(
        "instagram_accounts",
        {
            "id": f"eq.{instagram_account_id}",
            "select": "id,business_id,instagram_user_id,username,status",
        },
    )


def user_has_instagram_account_access(user_id, instagram_account_id):
    account = get_instagram_account_by_id(instagram_account_id)
    if not account:
        return None

    membership = _fetch_one(
        "business_users",
        {
            "business_id": f"eq.{account['business_id']}",
            "user_id": f"eq.{user_id}",
            "select": "id,role",
        },
    )
    return account if membership else None


def match_knowledge_chunks(instagram_account_id, query_embedding, match_count=5):
    if not instagram_account_id or not query_embedding:
        return []

    rows = _rpc(
        "match_knowledge_chunks",
        {
            "p_instagram_account_id": instagram_account_id,
            "p_query_embedding": query_embedding,
            "p_match_count": match_count,
        },
    )
    return rows or []


def ensure_contact(instagram_account_id, sender_id, username=None):
    row = {
        "instagram_account_id": instagram_account_id,
        "instagram_user_id": sender_id,
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
    }
    if username:
        row["username"] = username

    return _upsert("ig_contacts", row, "instagram_account_id,instagram_user_id")


def normalize_promo_code_prefix(prefix):
    normalized = re.sub(r"[^A-Za-z0-9]", "", prefix or "").upper()
    return (normalized or "PROMO")[:12]


def generate_promo_code(prefix=None):
    normalized_prefix = normalize_promo_code_prefix(prefix)
    suffix = "".join(secrets.choice(PROMO_CODE_ALPHABET) for _ in range(PROMO_CODE_SUFFIX_LENGTH))
    return f"{normalized_prefix}-{suffix}"


def ensure_promo_code(
    instagram_account_id,
    post_id,
    contact_id,
    comment_id,
    prefix=None,
    valid_duration_hours=None,
):
    existing = _fetch_one(
        "ig_promo_codes",
        {
            "post_id": f"eq.{post_id}",
            "contact_id": f"eq.{contact_id}",
            "select": (
                "id,instagram_account_id,post_id,contact_id,comment_id,code,status,"
                "valid_from,expires_at,redeemed_at,extra_metadata"
            ),
        },
    )
    if existing:
        if comment_id and not existing.get("comment_id"):
            _patch("ig_promo_codes", {"id": f"eq.{existing['id']}"}, {"comment_id": comment_id})
            existing["comment_id"] = comment_id
        return existing

    for _ in range(PROMO_CODE_MAX_ATTEMPTS):
        valid_from = datetime.now(timezone.utc)
        expires_at = None
        if valid_duration_hours:
            expires_at = valid_from + timedelta(hours=int(valid_duration_hours))

        row = {
            "instagram_account_id": instagram_account_id,
            "post_id": post_id,
            "contact_id": contact_id,
            "comment_id": comment_id,
            "code": generate_promo_code(prefix),
            "status": "issued",
            "valid_from": valid_from.isoformat(),
        }
        if expires_at:
            row["expires_at"] = expires_at.isoformat()

        try:
            return _insert("ig_promo_codes", row)
        except SupabaseError as exc:
            error_text = str(exc)
            if "ig_promo_codes_post_contact_key" in error_text:
                return _fetch_one(
                    "ig_promo_codes",
                    {
                        "post_id": f"eq.{post_id}",
                        "contact_id": f"eq.{contact_id}",
                        "select": (
                            "id,instagram_account_id,post_id,contact_id,comment_id,code,status,"
                            "valid_from,expires_at,redeemed_at,extra_metadata"
                        ),
                    },
                )
            if "ig_promo_codes_account_code_key" not in error_text:
                raise

    raise SupabaseError("Unable to generate a unique promo code after several attempts")


def is_promo_code_valid(code_row, now=None):
    if not code_row or code_row.get("status") != "issued":
        return False

    expires_at = code_row.get("expires_at")
    if not expires_at:
        return True

    now = now or datetime.now(timezone.utc)
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return now < expires_at


def expire_expired_promo_codes(now=None):
    now = now or datetime.now(timezone.utc)
    return _patch_returning(
        "ig_promo_codes",
        {
            "status": "eq.issued",
            "expires_at": f"lte.{now.isoformat()}",
            "select": "id,code,instagram_account_id,post_id,contact_id,expires_at,status",
        },
        {"status": "expired"},
    )


def get_promo_code_by_code(instagram_account_id, code):
    return _fetch_one(
        "ig_promo_codes",
        {
            "instagram_account_id": f"eq.{instagram_account_id}",
            "code": f"eq.{code}",
            "select": (
                "id,instagram_account_id,post_id,contact_id,comment_id,code,status,"
                "valid_from,expires_at,redeemed_at,extra_metadata,created_at,updated_at"
            ),
        },
    )


def redeem_promo_code(promo_code_id, redeemed_by=None):
    now = datetime.now(timezone.utc).isoformat()
    existing = _fetch_one(
        "ig_promo_codes",
        {
            "id": f"eq.{promo_code_id}",
            "select": "extra_metadata",
        },
    )
    extra_metadata = existing.get("extra_metadata") if existing else {}
    if not isinstance(extra_metadata, dict):
        extra_metadata = {}
    extra_metadata = {
        **extra_metadata,
        "redeemed_from": "frontend-login",
        "redeemed_by": redeemed_by,
    }

    rows = _patch_returning(
        "ig_promo_codes",
        {
            "id": f"eq.{promo_code_id}",
            "status": "eq.issued",
            "select": (
                "id,instagram_account_id,post_id,contact_id,comment_id,code,status,"
                "valid_from,expires_at,redeemed_at,extra_metadata,created_at,updated_at"
            ),
        },
        {
            "status": "redeemed",
            "redeemed_at": now,
            "extra_metadata": extra_metadata,
        },
    )
    return rows[0] if rows else None


def get_instagram_post(instagram_account_id, instagram_media_id):
    return _fetch_one(
        "ig_posts",
        {
            "instagram_account_id": f"eq.{instagram_account_id}",
            "instagram_media_id": f"eq.{instagram_media_id}",
            "select": (
                "id,instagram_account_id,instagram_media_id,caption,post_type,"
                "automation_enabled,automation_starts_at,automation_ends_at,"
                "trigger_keywords,comment_reply_text,dm_prompt,"
                "promo_code_valid_duration_hours,promotion_metadata"
            ),
        },
    )


def list_instagram_post_media_ids(instagram_account_id, limit=1000):
    rows = _request(
        "GET",
        "ig_posts",
        params={
            "instagram_account_id": f"eq.{instagram_account_id}",
            "select": "instagram_media_id",
            "limit": str(limit),
        },
    )
    return [row["instagram_media_id"] for row in rows or [] if row.get("instagram_media_id")]


def upsert_instagram_post(row):
    return _upsert("ig_posts", row, "instagram_account_id,instagram_media_id")


def create_promotion_setup(row):
    return _insert("ig_promotion_setups", row)


def get_promotion_setup(setup_id):
    return _fetch_one(
        "ig_promotion_setups",
        {
            "id": f"eq.{setup_id}",
            "select": PROMOTION_SETUP_SELECT,
        },
    )


def update_promotion_setup(setup_id, patch):
    rows = _patch_returning(
        "ig_promotion_setups",
        {"id": f"eq.{setup_id}", "select": PROMOTION_SETUP_SELECT},
        patch,
    )
    return rows[0] if rows else None


def get_comment_by_instagram_id(instagram_account_id, instagram_comment_id):
    return _fetch_one(
        "ig_comments",
        {
            "instagram_account_id": f"eq.{instagram_account_id}",
            "instagram_comment_id": f"eq.{instagram_comment_id}",
            "select": "id,post_id,contact_id,automation_status",
        },
    )


def has_prior_comment_automation(post_id, contact_id):
    if not post_id or not contact_id:
        return False

    existing = _fetch_one(
        "ig_comments",
        {
            "post_id": f"eq.{post_id}",
            "contact_id": f"eq.{contact_id}",
            "automation_status": "in.(pending,sent,comment_reply_failed,private_reply_failed,openai_failed,error)",
            "select": "id",
        },
    )
    return existing is not None


def upsert_comment(
    instagram_account_id,
    post_id,
    instagram_comment_id,
    text,
    contact_id=None,
    parent_comment_id=None,
    created_at_ig=None,
    automation_status="not_applicable",
    matched_keyword=None,
    extra_metadata=None,
):
    row = {
        "instagram_account_id": instagram_account_id,
        "post_id": post_id,
        "instagram_comment_id": instagram_comment_id,
        "text": text,
        "automation_status": automation_status,
        "extra_metadata": extra_metadata or {},
    }

    if contact_id:
        row["contact_id"] = contact_id
    if parent_comment_id:
        row["parent_comment_id"] = parent_comment_id
    if created_at_ig:
        row["created_at_ig"] = created_at_ig
    if matched_keyword:
        row["matched_keyword"] = matched_keyword

    return _upsert("ig_comments", row, "instagram_account_id,instagram_comment_id")


def update_comment_automation(
    comment_id,
    automation_status,
    public_reply_comment_id=None,
    private_reply_message_id=None,
    automation_error=None,
):
    patch = {"automation_status": automation_status}
    if public_reply_comment_id:
        patch["public_reply_comment_id"] = public_reply_comment_id
        patch["replied_to"] = True
    if private_reply_message_id:
        patch["private_reply_message_id"] = private_reply_message_id
    if automation_error:
        patch["automation_error"] = automation_error

    return _patch("ig_comments", {"id": f"eq.{comment_id}"}, patch)


def ensure_dm_session(instagram_account_id, contact_id):
    return _upsert(
        "ig_dm_sessions",
        {
            "instagram_account_id": instagram_account_id,
            "contact_id": contact_id,
            "status": "open",
            "last_activity_at": datetime.now(timezone.utc).isoformat(),
        },
        "instagram_account_id,contact_id",
    )


def message_exists(instagram_message_id):
    if not instagram_message_id:
        return False

    existing = _fetch_one(
        "ig_dm_messages",
        {
            "instagram_message_id": f"eq.{instagram_message_id}",
            "select": "id",
        },
    )
    return existing is not None


def insert_dm_message(
    session_id,
    contact_id,
    role,
    direction,
    content,
    instagram_message_id=None,
    delivery_status="complete",
    created_at=None,
    model=None,
    token_usage=None,
    latency_ms=None,
    error_message=None,
):
    row = {
        "session_id": session_id,
        "contact_id": contact_id,
        "role": role,
        "direction": direction,
        "content": content,
        "delivery_status": delivery_status,
        "token_usage": token_usage or {},
    }

    if instagram_message_id:
        row["instagram_message_id"] = instagram_message_id
    if created_at:
        row["created_at"] = created_at
    if model:
        row["model"] = model
    if latency_ms is not None:
        row["latency_ms"] = latency_ms
    if error_message:
        row["error_message"] = error_message

    return _insert("ig_dm_messages", row)


def fetch_dm_history(session_id, limit=20):
    rows = _request(
        "GET",
        "ig_dm_messages",
        params={
            "session_id": f"eq.{session_id}",
            "select": "role,content,created_at",
            "order": "created_at.desc",
            "limit": str(limit),
        },
    )
    rows = rows or []
    return [
        {"role": row["role"], "content": row["content"]}
        for row in reversed(rows)
        if row.get("role") in {"user", "assistant", "system"} and row.get("content")
    ]


def upsert_dm_session_state(session_id, last_response_id=None, last_intent=None, summary=None):
    row = {
        "session_id": session_id,
        "last_response_id": last_response_id,
        "last_intent": last_intent,
        "summary": summary,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    return _upsert("ig_dm_session_state", row, "session_id")


def touch_dm_session(session_id):
    return _patch(
        "ig_dm_sessions",
        {"id": f"eq.{session_id}"},
        {"last_activity_at": datetime.now(timezone.utc).isoformat()},
    )


def upsert_meta_webhook_event(
    event_id,
    business_id,
    event_type,
    payload,
    instagram_account_id=None,
    processing_status="received",
    error_message=None,
):
    if not event_id:
        return None

    row = {
        "event_id": event_id,
        "business_id": business_id,
        "event_type": event_type,
        "payload": payload or {},
        "processing_status": processing_status,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }

    if instagram_account_id:
        row["instagram_account_id"] = instagram_account_id
    if error_message:
        row["error_message"] = error_message

    return _upsert("meta_webhook_events", row, "event_id")
