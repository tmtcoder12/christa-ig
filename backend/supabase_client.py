import json
import os
import re
import secrets
import string
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from christa_ig.http_client import DEFAULT_TIMEOUT_SECONDS, perform_request

PROMO_CODE_ALPHABET = string.ascii_uppercase + string.digits
PROMO_CODE_SUFFIX_LENGTH = 6
PROMO_CODE_MAX_ATTEMPTS = 8
PROMOTION_SETUP_SELECT = (
    "id,instagram_account_id,submitted_by,comment_trigger_mode,trigger_keywords,"
    "automation_starts_at,automation_ends_at,promo_code_valid_duration_hours,"
    "comment_reply_text,dm_prompt,code_prefix,baseline_media_ids,status,post_id,"
    "found_instagram_media_id,found_caption,error_message,poll_started_at,"
    "poll_expires_at,last_polled_at,found_at,next_poll_at,locked_at,locked_until,"
    "locked_by,worker_attempt_count,last_worker_error,extra_metadata,created_at,updated_at"
)
WEBHOOK_JOB_SELECT = (
    "id,provider,external_event_id,event_type,account_external_id,payload,status,"
    "attempt_count,available_at,locked_at,locked_until,locked_by,error_code,"
    "error_message,request_id,completed_at,created_at,updated_at"
)
KNOWLEDGE_CHUNK_SELECT = (
    "id,instagram_account_id,text,type,source_url,page_path,title,"
    "meta_description,extra_metadata,content_hash,created_at"
)
PROMO_CODE_FOLLOWUP_SELECT = (
    "id,promo_code_id,instagram_account_id,contact_id,scheduled_for,sent_at,status,"
    "message_text,message_tag,instagram_message_id,error_message,attempt_count,"
    "extra_metadata,created_at,updated_at"
)
PROMO_LEAD_SELECT = (
    "id,instagram_account_id,post_id,contact_id,comment_id,promo_code_id,"
    "customer_name,phone_raw,phone_e164,sms_consent_at,status,error_message,"
    "extra_metadata,created_at,updated_at"
)
SMS_MESSAGE_SELECT = (
    "id,instagram_account_id,contact_id,promo_code_id,promo_lead_id,to_phone_e164,"
    "body,purpose,status,scheduled_for,sent_at,twilio_message_sid,error_message,"
    "attempt_count,extra_metadata,created_at,updated_at"
)
SMS_CONVERSATION_SELECT = (
    "id,instagram_account_id,contact_id,promo_lead_id,phone_e164,status,"
    "last_message_at,closed_at,extra_metadata,created_at,updated_at"
)
SMS_CONVERSATION_MESSAGE_SELECT = (
    "id,conversation_id,instagram_account_id,contact_id,promo_lead_id,role,"
    "direction,body,twilio_message_sid,delivery_status,model,response_id,"
    "token_usage,latency_ms,error_message,raw_payload,created_at"
)
CUSTOMER_PROFILE_SELECT = (
    "id,instagram_account_id,contact_id,phone_e164,display_name,first_redeemed_at,"
    "last_redeemed_at,redeem_count,last_order_notes,profile_summary,extra_metadata,"
    "created_at,updated_at"
)
COMMENT_CLASSIFICATION_SELECT = (
    "id,comment_id,business_id,model,classification,confidence,reasoning,status,error_message,classified_at"
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
        response = perform_request(
            api_request,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            retry_safe=method in {"GET", "HEAD"},
        )
        response_body = response.body.decode("utf-8")
        if not response_body:
            return None
        return json.loads(response_body)
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise SupabaseError(f"Supabase {method} {path} failed: {exc.code} {error_body}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        raise SupabaseError(f"Supabase {method} {path} failed: {reason}") from exc
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
            "select": "id,business_id,instagram_user_id,username,status,system_prompt",
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


def list_knowledge_chunks(instagram_account_id, limit=20, offset=0, chunk_type=None, category=None):
    params = {
        "instagram_account_id": f"eq.{instagram_account_id}",
        "select": KNOWLEDGE_CHUNK_SELECT,
        "order": "created_at.desc",
        "limit": str(limit),
        "offset": str(offset),
    }
    if chunk_type:
        params["type"] = f"eq.{chunk_type}"
    if category:
        params["extra_metadata->>category"] = f"eq.{category}"

    rows = _request(
        "GET",
        "knowledge_chunks",
        params=params,
    )
    return rows or []


def list_knowledge_chunk_filter_values(instagram_account_id, limit=1000):
    rows = _request(
        "GET",
        "knowledge_chunks",
        params={
            "instagram_account_id": f"eq.{instagram_account_id}",
            "select": "type,extra_metadata",
            "limit": str(limit),
        },
    )
    types = set()
    categories = set()
    for row in rows or []:
        chunk_type = row.get("type")
        if chunk_type:
            types.add(str(chunk_type))

        extra_metadata = row.get("extra_metadata") or {}
        if isinstance(extra_metadata, dict):
            category = extra_metadata.get("category")
            if category:
                categories.add(str(category))

    return {
        "types": sorted(types, key=str.casefold),
        "categories": sorted(categories, key=str.casefold),
    }


def insert_knowledge_chunk(row):
    rows = _request(
        "POST",
        "knowledge_chunks",
        params={"select": KNOWLEDGE_CHUNK_SELECT},
        payload=row,
        prefer="return=representation",
    )
    if not rows:
        return None
    return rows[0]


def insert_comment_classification(row):
    rows = _request(
        "POST",
        "ig_comment_classifications",
        params={"select": COMMENT_CLASSIFICATION_SELECT},
        payload=row,
        prefer="return=representation",
    )
    if not rows:
        return None
    return rows[0]


def ensure_contact(instagram_account_id, sender_id, username=None):
    row = {
        "instagram_account_id": instagram_account_id,
        "instagram_user_id": sender_id,
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
    }
    if username:
        row["username"] = username

    return _upsert("ig_contacts", row, "instagram_account_id,instagram_user_id")


def update_contact_sms_details(contact_id, customer_name=None, phone_raw=None, phone_e164=None, sms_consent_at=None):
    patch = {
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
    }
    if customer_name:
        patch["display_name"] = customer_name
    if phone_raw:
        patch["phone_raw"] = phone_raw
    if phone_e164:
        patch["phone_e164"] = phone_e164
    if sms_consent_at:
        patch["sms_consent_at"] = sms_consent_at

    rows = _patch_returning(
        "ig_contacts",
        {
            "id": f"eq.{contact_id}",
            "select": "id,instagram_account_id,instagram_user_id,username,display_name,phone_raw,phone_e164,sms_consent_at",
        },
        patch,
    )
    return rows[0] if rows else None


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


def get_promo_code_by_id(promo_code_id):
    return _fetch_one(
        "ig_promo_codes",
        {
            "id": f"eq.{promo_code_id}",
            "select": (
                "id,instagram_account_id,post_id,contact_id,comment_id,code,status,"
                "valid_from,expires_at,redeemed_at,extra_metadata,created_at,updated_at"
            ),
        },
    )


def redeem_promo_code(promo_code_id, redeemed_by=None, redemption_notes=None):
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
    if redemption_notes:
        extra_metadata["redemption_notes"] = redemption_notes

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


def build_customer_profile_summary(existing_summary=None, display_name=None, order_notes=None):
    summary_parts = []
    if existing_summary:
        summary_parts.append(str(existing_summary).strip())
    if order_notes:
        note_prefix = f"{display_name} ordered" if display_name else "Customer ordered"
        summary_parts.append(f"{note_prefix}: {order_notes.strip()}")
    summary = "\n".join(part for part in summary_parts if part)
    return summary or None


def ensure_customer_profile_from_redemption(
    instagram_account_id,
    contact_id,
    phone_e164,
    display_name=None,
    redeemed_at=None,
    order_notes=None,
    redeemed_by=None,
    promo_code_id=None,
):
    if not phone_e164:
        return None

    redeemed_at = redeemed_at or datetime.now(timezone.utc).isoformat()
    existing = _fetch_one(
        "ig_customer_profiles",
        {
            "instagram_account_id": f"eq.{instagram_account_id}",
            "phone_e164": f"eq.{phone_e164}",
            "select": CUSTOMER_PROFILE_SELECT,
        },
    )
    existing_metadata = existing.get("extra_metadata") if existing else {}
    if not isinstance(existing_metadata, dict):
        existing_metadata = {}

    redemption_events = existing_metadata.get("redemption_events")
    if not isinstance(redemption_events, list):
        redemption_events = []
    redemption_events = [
        *redemption_events[-19:],
        {
            "redeemed_at": redeemed_at,
            "redeemed_by": redeemed_by,
            "promo_code_id": promo_code_id,
            "order_notes": order_notes,
        },
    ]
    extra_metadata = {
        **existing_metadata,
        "last_redeemed_by": redeemed_by,
        "last_promo_code_id": promo_code_id,
        "redemption_events": redemption_events,
    }

    if existing:
        patch = {
            "contact_id": contact_id or existing.get("contact_id"),
            "display_name": display_name or existing.get("display_name"),
            "last_redeemed_at": redeemed_at,
            "redeem_count": int(existing.get("redeem_count") or 0) + 1,
            "last_order_notes": order_notes if order_notes is not None else existing.get("last_order_notes"),
            "profile_summary": build_customer_profile_summary(
                existing.get("profile_summary"),
                display_name or existing.get("display_name"),
                order_notes,
            ),
            "extra_metadata": extra_metadata,
        }
        rows = _patch_returning(
            "ig_customer_profiles",
            {"id": f"eq.{existing['id']}", "select": CUSTOMER_PROFILE_SELECT},
            patch,
        )
        return rows[0] if rows else existing

    row = {
        "instagram_account_id": instagram_account_id,
        "contact_id": contact_id,
        "phone_e164": phone_e164,
        "display_name": display_name,
        "first_redeemed_at": redeemed_at,
        "last_redeemed_at": redeemed_at,
        "redeem_count": 1,
        "last_order_notes": order_notes,
        "profile_summary": build_customer_profile_summary(None, display_name, order_notes),
        "extra_metadata": extra_metadata,
    }
    return _insert("ig_customer_profiles", row)


def ensure_promo_code_followup(
    promo_code,
    scheduled_for,
    message_text,
    message_tag="NOTIFICATION_MESSAGE",
    extra_metadata=None,
):
    existing = _fetch_one(
        "ig_promo_code_followups",
        {
            "promo_code_id": f"eq.{promo_code['id']}",
            "select": PROMO_CODE_FOLLOWUP_SELECT,
        },
    )
    if existing:
        return existing

    row = {
        "promo_code_id": promo_code["id"],
        "instagram_account_id": promo_code["instagram_account_id"],
        "contact_id": promo_code["contact_id"],
        "scheduled_for": scheduled_for,
        "message_text": message_text,
        "message_tag": message_tag,
        "status": "pending",
        "extra_metadata": extra_metadata or {},
    }
    try:
        return _insert("ig_promo_code_followups", row)
    except SupabaseError as exc:
        if "ig_promo_code_followups_promo_code_key" not in str(exc):
            raise
        return _fetch_one(
            "ig_promo_code_followups",
            {
                "promo_code_id": f"eq.{promo_code['id']}",
                "select": PROMO_CODE_FOLLOWUP_SELECT,
            },
        )


def list_due_promo_code_followups(now=None, limit=20):
    now = now or datetime.now(timezone.utc)
    rows = _request(
        "GET",
        "ig_promo_code_followups",
        params={
            "status": "eq.pending",
            "scheduled_for": f"lte.{now.isoformat()}",
            "select": PROMO_CODE_FOLLOWUP_SELECT,
            "order": "scheduled_for.asc",
            "limit": str(limit),
        },
    )
    return rows or []


def claim_promo_code_followup(followup_id):
    rows = _patch_returning(
        "ig_promo_code_followups",
        {
            "id": f"eq.{followup_id}",
            "status": "eq.pending",
            "select": PROMO_CODE_FOLLOWUP_SELECT,
        },
        {"status": "sending"},
    )
    return rows[0] if rows else None


def mark_promo_code_followup_sent(followup_id, instagram_message_id=None, extra_metadata=None):
    patch = {
        "status": "sent",
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "error_message": None,
    }
    if instagram_message_id:
        patch["instagram_message_id"] = instagram_message_id
    if extra_metadata is not None:
        patch["extra_metadata"] = extra_metadata

    rows = _patch_returning(
        "ig_promo_code_followups",
        {"id": f"eq.{followup_id}", "select": PROMO_CODE_FOLLOWUP_SELECT},
        patch,
    )
    return rows[0] if rows else None


def mark_promo_code_followup_failed(followup, error_message, extra_metadata=None):
    patch = {
        "status": "failed",
        "error_message": error_message,
        "attempt_count": int(followup.get("attempt_count") or 0) + 1,
    }
    if extra_metadata is not None:
        patch["extra_metadata"] = extra_metadata

    rows = _patch_returning(
        "ig_promo_code_followups",
        {"id": f"eq.{followup['id']}", "select": PROMO_CODE_FOLLOWUP_SELECT},
        patch,
    )
    return rows[0] if rows else None


def get_contact_by_id(contact_id):
    return _fetch_one(
        "ig_contacts",
        {
            "id": f"eq.{contact_id}",
            "select": "id,instagram_account_id,instagram_user_id,username,display_name,phone_raw,phone_e164,sms_consent_at",
        },
    )


def ensure_promo_lead(
    instagram_account_id,
    post_id,
    contact_id,
    comment_id,
    promo_code_id,
    extra_metadata=None,
):
    existing = _fetch_one(
        "ig_promo_leads",
        {
            "promo_code_id": f"eq.{promo_code_id}",
            "select": PROMO_LEAD_SELECT,
        },
    )
    if existing:
        return existing

    row = {
        "instagram_account_id": instagram_account_id,
        "post_id": post_id,
        "contact_id": contact_id,
        "comment_id": comment_id,
        "promo_code_id": promo_code_id,
        "status": "collecting",
        "extra_metadata": extra_metadata or {},
    }
    try:
        return _insert("ig_promo_leads", row)
    except SupabaseError as exc:
        if "ig_promo_leads_promo_code_key" not in str(exc):
            raise
        return _fetch_one(
            "ig_promo_leads",
            {
                "promo_code_id": f"eq.{promo_code_id}",
                "select": PROMO_LEAD_SELECT,
            },
        )


def get_collecting_promo_lead(contact_id):
    return _fetch_one(
        "ig_promo_leads",
        {
            "contact_id": f"eq.{contact_id}",
            "status": "eq.collecting",
            "select": PROMO_LEAD_SELECT,
            "order": "created_at.desc",
        },
    )


def get_promo_lead_by_promo_code(promo_code_id):
    return _fetch_one(
        "ig_promo_leads",
        {
            "promo_code_id": f"eq.{promo_code_id}",
            "select": PROMO_LEAD_SELECT,
        },
    )


def get_latest_promo_lead_by_phone(phone_e164):
    return _fetch_one(
        "ig_promo_leads",
        {
            "phone_e164": f"eq.{phone_e164}",
            "status": "neq.cancelled",
            "select": PROMO_LEAD_SELECT,
            "order": "created_at.desc",
        },
    )


def update_promo_lead(lead_id, patch):
    rows = _patch_returning(
        "ig_promo_leads",
        {"id": f"eq.{lead_id}", "select": PROMO_LEAD_SELECT},
        patch,
    )
    return rows[0] if rows else None


def create_sms_message(row):
    return _insert("ig_sms_messages", row)


def ensure_sms_conversation(instagram_account_id, contact_id, phone_e164, promo_lead_id=None, extra_metadata=None):
    existing = _fetch_one(
        "ig_sms_conversations",
        {
            "instagram_account_id": f"eq.{instagram_account_id}",
            "phone_e164": f"eq.{phone_e164}",
            "select": SMS_CONVERSATION_SELECT,
        },
    )
    if existing:
        patch = {
            "contact_id": contact_id,
            "last_message_at": datetime.now(timezone.utc).isoformat(),
        }
        if promo_lead_id:
            patch["promo_lead_id"] = promo_lead_id
        rows = _patch_returning(
            "ig_sms_conversations",
            {"id": f"eq.{existing['id']}", "select": SMS_CONVERSATION_SELECT},
            patch,
        )
        return rows[0] if rows else existing

    row = {
        "instagram_account_id": instagram_account_id,
        "contact_id": contact_id,
        "phone_e164": phone_e164,
        "last_message_at": datetime.now(timezone.utc).isoformat(),
    }
    if promo_lead_id:
        row["promo_lead_id"] = promo_lead_id
    if extra_metadata:
        row["extra_metadata"] = extra_metadata

    return _upsert("ig_sms_conversations", row, "instagram_account_id,phone_e164")


def touch_sms_conversation(conversation_id):
    return _patch(
        "ig_sms_conversations",
        {"id": f"eq.{conversation_id}"},
        {"last_message_at": datetime.now(timezone.utc).isoformat()},
    )


def close_sms_conversation(conversation_id, extra_metadata=None):
    patch = {
        "status": "closed",
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "last_message_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra_metadata is not None:
        patch["extra_metadata"] = extra_metadata

    rows = _patch_returning(
        "ig_sms_conversations",
        {"id": f"eq.{conversation_id}", "select": SMS_CONVERSATION_SELECT},
        patch,
    )
    return rows[0] if rows else None


def sms_conversation_message_exists(twilio_message_sid):
    if not twilio_message_sid:
        return False
    return bool(
        _fetch_one(
            "ig_sms_conversation_messages",
            {
                "twilio_message_sid": f"eq.{twilio_message_sid}",
                "select": "id",
            },
        )
    )


def insert_sms_conversation_message(row):
    rows = _request(
        "POST",
        "ig_sms_conversation_messages",
        params={"select": SMS_CONVERSATION_MESSAGE_SELECT},
        payload=row,
        prefer="return=representation",
    )
    if not rows:
        return None
    return rows[0]


def fetch_sms_conversation_history(conversation_id, limit=20):
    rows = _request(
        "GET",
        "ig_sms_conversation_messages",
        params={
            "conversation_id": f"eq.{conversation_id}",
            "select": "role,body,created_at",
            "order": "created_at.desc",
            "limit": str(limit),
        },
    )
    rows = rows or []
    return [
        {"role": row["role"], "content": row["body"]}
        for row in reversed(rows)
        if row.get("role") in {"user", "assistant", "system"} and row.get("body")
    ]


def get_redemption_followup_sms_by_promo_code(promo_code_id):
    return _fetch_one(
        "ig_sms_messages",
        {
            "promo_code_id": f"eq.{promo_code_id}",
            "purpose": "eq.post_redemption_followup",
            "select": SMS_MESSAGE_SELECT,
        },
    )


def list_due_sms_messages(now=None, limit=20):
    now = now or datetime.now(timezone.utc)
    rows = _request(
        "GET",
        "ig_sms_messages",
        params={
            "status": "eq.pending",
            "scheduled_for": f"lte.{now.isoformat()}",
            "select": SMS_MESSAGE_SELECT,
            "order": "scheduled_for.asc",
            "limit": str(limit),
        },
    )
    return rows or []


def claim_sms_message(sms_message_id):
    rows = _patch_returning(
        "ig_sms_messages",
        {
            "id": f"eq.{sms_message_id}",
            "status": "eq.pending",
            "select": SMS_MESSAGE_SELECT,
        },
        {"status": "sending"},
    )
    return rows[0] if rows else None


def mark_sms_message_sent(sms_message_id, twilio_message_sid=None, extra_metadata=None):
    patch = {
        "status": "sent",
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "error_message": None,
    }
    if twilio_message_sid:
        patch["twilio_message_sid"] = twilio_message_sid
    if extra_metadata is not None:
        patch["extra_metadata"] = extra_metadata

    rows = _patch_returning(
        "ig_sms_messages",
        {"id": f"eq.{sms_message_id}", "select": SMS_MESSAGE_SELECT},
        patch,
    )
    return rows[0] if rows else None


def mark_sms_message_failed(sms_message, error_message, extra_metadata=None):
    patch = {
        "status": "failed",
        "error_message": error_message,
        "attempt_count": int(sms_message.get("attempt_count") or 0) + 1,
    }
    if extra_metadata is not None:
        patch["extra_metadata"] = extra_metadata

    rows = _patch_returning(
        "ig_sms_messages",
        {"id": f"eq.{sms_message['id']}", "select": SMS_MESSAGE_SELECT},
        patch,
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
                "automation_enabled,comment_trigger_mode,automation_starts_at,"
                "automation_ends_at,trigger_keywords,comment_reply_text,dm_prompt,"
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


def claim_promotion_setups(batch_size, worker_id, lease_seconds=300):
    return (
        _rpc(
            "claim_promotion_setups",
            {
                "p_batch_size": batch_size,
                "p_worker_id": worker_id,
                "p_lease_seconds": lease_seconds,
            },
        )
        or []
    )


def enqueue_webhook_jobs(jobs, request_id=None):
    if not jobs:
        return []
    rows = [{**job, "request_id": request_id} for job in jobs]
    return (
        _request(
            "POST",
            "webhook_jobs",
            params={"on_conflict": "provider,external_event_id"},
            payload=rows,
            prefer="resolution=ignore-duplicates,return=representation",
        )
        or []
    )


def claim_webhook_jobs(batch_size, worker_id, lease_seconds=300):
    return (
        _rpc(
            "claim_webhook_jobs",
            {
                "p_batch_size": batch_size,
                "p_worker_id": worker_id,
                "p_lease_seconds": lease_seconds,
            },
        )
        or []
    )


def complete_webhook_job(job_id, status="succeeded", error_code=None, error_message=None):
    patch = {
        "status": status,
        "payload": {},
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "locked_at": None,
        "locked_until": None,
        "locked_by": None,
        "error_code": error_code,
        "error_message": error_message,
    }
    rows = _patch_returning(
        "webhook_jobs",
        {"id": f"eq.{job_id}", "select": WEBHOOK_JOB_SELECT},
        patch,
    )
    return rows[0] if rows else None


def retry_webhook_job(job_id, available_at, error_code, error_message):
    rows = _patch_returning(
        "webhook_jobs",
        {"id": f"eq.{job_id}", "select": WEBHOOK_JOB_SELECT},
        {
            "status": "queued",
            "available_at": available_at,
            "locked_at": None,
            "locked_until": None,
            "locked_by": None,
            "error_code": error_code,
            "error_message": str(error_message or "")[:2000],
        },
    )
    return rows[0] if rows else None


def fail_webhook_job(job_id, error_code, error_message):
    rows = _patch_returning(
        "webhook_jobs",
        {"id": f"eq.{job_id}", "select": WEBHOOK_JOB_SELECT},
        {
            "status": "failed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "locked_at": None,
            "locked_until": None,
            "locked_by": None,
            "error_code": error_code,
            "error_message": str(error_message or "")[:2000],
        },
    )
    return rows[0] if rows else None


def delete_expired_failed_webhook_jobs(retention_days):
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    return _request(
        "DELETE",
        "webhook_jobs",
        params={
            "status": "eq.failed",
            "completed_at": f"lt.{cutoff.isoformat()}",
        },
        prefer="return=minimal",
    )


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
    matched_keyword=None,
):
    patch = {"automation_status": automation_status}
    if matched_keyword:
        patch["matched_keyword"] = matched_keyword
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
    query_type=None,
    sources=None,
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
    if query_type:
        row["query_type"] = query_type
    if sources is not None:
        row["sources"] = sources
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
        "payload": summarize_webhook_payload(payload),
        "processing_status": processing_status,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }

    if instagram_account_id:
        row["instagram_account_id"] = instagram_account_id
    if error_message:
        row["error_message"] = error_message

    return _upsert("meta_webhook_events", row, "event_id")


def summarize_webhook_payload(payload):
    """Keep operational shape without duplicating message text or phone data."""
    if not isinstance(payload, dict):
        return {}
    summary = {"keys": sorted(str(key) for key in payload.keys())}
    if payload.get("object"):
        summary["object"] = str(payload["object"])
    entries = payload.get("entry")
    if isinstance(entries, list):
        summary["entry_count"] = len(entries)
    message_sid = payload.get("MessageSid") or payload.get("SmsMessageSid") or payload.get("SmsSid")
    if message_sid:
        summary["message_sid"] = str(message_sid)
    return summary
