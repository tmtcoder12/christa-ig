import json
import os

from openai import OpenAI


DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant responding to Instagram direct messages."
DEFAULT_FALLBACK_MESSAGE = "Thanks for your message — I'll get back to you shortly."
MAX_HISTORY_MESSAGES = 20
MAX_KNOWLEDGE_CHARS_PER_CHUNK = 900
MAX_PUBLIC_COMMENT_REPLY_CHARS = 220
MAX_PROMO_DM_CHARS = 900
MAX_FOLLOWUP_SMS_CHARS = 700
COMMENT_CLASSIFIER_SYSTEM_PROMPT = (
    "Classify Instagram comments on restaurant promotional posts. "
    "Trigger only for positive or neutral genuine restaurant/customer intent: menu, dietary, "
    "hours, location, reservations, pricing, availability, purchase intent, or positive "
    "experience comments. Do not trigger for complaints, negative feedback, spam, tag-only "
    "comments, emoji-only comments, or unrelated text. Return only JSON."
)
LEAD_EXTRACTION_SYSTEM_PROMPT = (
    "Extract customer contact information from Instagram DMs. "
    "Return only JSON with keys customer_name and phone. Use null when missing."
)
PROMO_COPY_SYSTEM_PROMPT = (
    "Write concise Instagram copy for a restaurant promotional comment automation. "
    "Use provided business knowledge only when relevant. Do not mention internal chunk IDs. "
    "Do not reveal the promo code in Instagram. Return only JSON."
)
FOLLOWUP_SMS_SYSTEM_PROMPT = (
    "Write a concise SMS follow-up for a restaurant guest after they redeemed a promo code. "
    "Use staff notes naturally when provided. No markdown. Return only the SMS body."
)


def _trim_history(history):
    return history[-MAX_HISTORY_MESSAGES:]


def _serialize_usage(response):
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}

    if hasattr(usage, "model_dump"):
        return usage.model_dump()

    if isinstance(usage, dict):
        return usage

    return {}


def _parse_json_object(raw_text):
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("Empty JSON response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def _coerce_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0", ""}:
            return False
    return bool(value)


def _compact_text(text, max_chars):
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= max_chars:
        return normalized
    suffix = "..."
    return f"{normalized[: max_chars - len(suffix)].rstrip()}{suffix}"


def _build_instructions(base_prompt, extra_prompt):
    base = (base_prompt or os.environ.get("OPENAI_SYSTEM_PROMPT") or DEFAULT_SYSTEM_PROMPT).strip()
    extra = str(extra_prompt or "").strip()
    return f"{base}\n\n{extra}" if extra else base


def generate_query_embedding(text):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    model = os.environ.get("OPENAI_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
    client = OpenAI(api_key=api_key)
    response = client.embeddings.create(model=model, input=text)
    embedding = response.data[0].embedding
    return [float(value) for value in embedding]


def _format_knowledge_context(knowledge_context):
    if not knowledge_context:
        return None

    sections = ["Business knowledge:"]
    for index, chunk in enumerate(knowledge_context, start=1):
        title = chunk.get("title") or chunk.get("type") or "Knowledge"
        source = chunk.get("source_url") or chunk.get("page_path")
        text = str(chunk.get("text") or "").strip()
        if not text:
            continue
        if len(text) > MAX_KNOWLEDGE_CHARS_PER_CHUNK:
            text = f"{text[:MAX_KNOWLEDGE_CHARS_PER_CHUNK].rstrip()}..."

        heading = f"[{index}] {title}"
        if source:
            heading = f"{heading} ({source})"
        sections.extend([heading, text])

    if len(sections) == 1:
        return None
    return "\n".join(sections)


def _build_input(history, knowledge_context):
    formatted_context = _format_knowledge_context(knowledge_context)
    if not formatted_context:
        return history

    return [
        {
            "role": "user",
            "content": (
                f"{formatted_context}\n\n"
                "Use this business knowledge when it is relevant to the user's message. "
                "Do not mention internal chunk IDs."
            ),
        },
        *history,
    ]


def generate_reply(history, system_prompt=None, knowledge_context=None):
    api_key = os.environ.get("OPENAI_API_KEY")
    fallback_message = os.environ.get("OPENAI_FALLBACK_MESSAGE", DEFAULT_FALLBACK_MESSAGE)
    knowledge_context_count = len(knowledge_context or [])

    if not api_key:
        return {
            "success": False,
            "reply_text": fallback_message,
            "error": "OPENAI_API_KEY is not set",
            "used_fallback": True,
            "model": os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
            "response_id": None,
            "token_usage": {},
            "knowledge_context_count": knowledge_context_count,
        }

    client = OpenAI(api_key=api_key)
    model = os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)
    system_prompt = system_prompt or os.environ.get("OPENAI_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT)
    trimmed_history = _trim_history(history)
    response_input = _build_input(trimmed_history, knowledge_context)

    try:
        response = client.responses.create(
            model=model,
            instructions=system_prompt,
            input=response_input,
        )
        reply_text = (response.output_text or "").strip()
        if reply_text:
            return {
                "success": True,
                "reply_text": reply_text,
                "error": None,
                "used_fallback": False,
                "model": model,
                "response_id": getattr(response, "id", None),
                "token_usage": _serialize_usage(response),
                "knowledge_context_count": knowledge_context_count,
            }

        return {
            "success": False,
            "reply_text": fallback_message,
            "error": "OpenAI response did not contain text",
            "used_fallback": True,
            "model": model,
            "response_id": getattr(response, "id", None),
            "token_usage": _serialize_usage(response),
            "knowledge_context_count": knowledge_context_count,
        }
    except Exception as exc:
        return {
            "success": False,
            "reply_text": fallback_message,
            "error": str(exc),
            "used_fallback": True,
            "model": model,
            "response_id": None,
            "token_usage": {},
            "knowledge_context_count": knowledge_context_count,
        }


def classify_restaurant_comment_for_promo(comment_text, post_caption=None):
    api_key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("COMMENT_CLASSIFIER_MODEL") or os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)
    if not api_key:
        return {
            "success": False,
            "should_trigger": False,
            "category": None,
            "confidence": None,
            "reasoning": None,
            "error": "OPENAI_API_KEY is not set",
            "model": model,
            "response_id": None,
            "token_usage": {},
        }

    client = OpenAI(api_key=api_key)
    prompt = (
        "Classify this Instagram comment for whether it should trigger a restaurant promo DM.\n\n"
        f"Post caption: {post_caption or ''}\n"
        f"Comment: {comment_text or ''}\n\n"
        "Return compact JSON with exactly these keys:\n"
        "- should_trigger: boolean\n"
        "- category: one of dietary_question, menu_question, hours_location_question, "
        "reservation_question, pricing_question, availability_question, purchase_intent, "
        "positive_experience, general_restaurant_comment, spam_or_unrelated, complaint_or_negative\n"
        "- confidence: number from 0 to 1\n"
        "- reasoning: short phrase"
    )

    try:
        response = client.responses.create(
            model=model,
            instructions=COMMENT_CLASSIFIER_SYSTEM_PROMPT,
            input=[{"role": "user", "content": prompt}],
        )
        parsed = _parse_json_object(response.output_text)
        if not isinstance(parsed, dict):
            raise ValueError("Classifier response was not a JSON object")

        confidence = parsed.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None

        return {
            "success": True,
            "should_trigger": _coerce_bool(parsed.get("should_trigger")),
            "category": parsed.get("category"),
            "confidence": confidence,
            "reasoning": parsed.get("reasoning"),
            "error": None,
            "model": model,
            "response_id": getattr(response, "id", None),
            "token_usage": _serialize_usage(response),
        }
    except Exception as exc:
        return {
            "success": False,
            "should_trigger": False,
            "category": None,
            "confidence": None,
            "reasoning": None,
            "error": str(exc),
            "model": model,
            "response_id": None,
            "token_usage": {},
        }


def generate_restaurant_intent_promo_messages(
    comment_text,
    post_caption=None,
    dm_prompt=None,
    matched_trigger=None,
    code_prefix=None,
    system_prompt=None,
    knowledge_context=None,
):
    api_key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)
    knowledge_context_count = len(knowledge_context or [])
    if not api_key:
        return {
            "success": False,
            "public_comment_reply": None,
            "private_dm": None,
            "error": "OPENAI_API_KEY is not set",
            "model": model,
            "response_id": None,
            "token_usage": {},
            "knowledge_context_count": knowledge_context_count,
        }

    formatted_context = _format_knowledge_context(knowledge_context)
    promotion_hint = (
        f"The promo code prefix/context is {code_prefix}. "
        "Do not include the actual code because it is texted later."
        if code_prefix
        else "There is a promo code available, but do not include it because it is texted later."
    )
    prompt = (
        "Generate copy for this restaurant promotional comment automation.\n\n"
        f"Post caption: {post_caption or ''}\n"
        f"Customer comment: {comment_text or ''}\n"
        f"Matched trigger/category: {matched_trigger or ''}\n"
        f"Promotion instructions: {dm_prompt or ''}\n"
        f"{promotion_hint}\n\n"
        "Return compact JSON with exactly these keys:\n"
        "- public_comment_reply: max 180 characters, natural, directly helpful if possible, "
        "and explicitly says we sent a DM or to check DMs.\n"
        "- private_dm: concise, answers the comment using relevant info, then asks for their "
        "name and phone number so we can text the promo code. Do not include a promo code."
    )
    if formatted_context:
        prompt = f"{formatted_context}\n\n{prompt}"

    client = OpenAI(api_key=api_key)
    try:
        response = client.responses.create(
            model=model,
            instructions=_build_instructions(system_prompt, PROMO_COPY_SYSTEM_PROMPT),
            input=[{"role": "user", "content": prompt}],
        )
        parsed = _parse_json_object(response.output_text)
        if not isinstance(parsed, dict):
            raise ValueError("Promo copy response was not a JSON object")

        public_comment_reply = _compact_text(
            parsed.get("public_comment_reply"),
            MAX_PUBLIC_COMMENT_REPLY_CHARS,
        )
        private_dm = _compact_text(parsed.get("private_dm"), MAX_PROMO_DM_CHARS)
        if not public_comment_reply or not private_dm:
            raise ValueError("Promo copy response did not include both messages")

        return {
            "success": True,
            "public_comment_reply": public_comment_reply,
            "private_dm": private_dm,
            "error": None,
            "model": model,
            "response_id": getattr(response, "id", None),
            "token_usage": _serialize_usage(response),
            "knowledge_context_count": knowledge_context_count,
        }
    except Exception as exc:
        return {
            "success": False,
            "public_comment_reply": None,
            "private_dm": None,
            "error": str(exc),
            "model": model,
            "response_id": None,
            "token_usage": {},
            "knowledge_context_count": knowledge_context_count,
        }


def generate_redemption_followup_sms(
    promo_code,
    display_name=None,
    redemption_notes=None,
    last_order_notes=None,
    profile_summary=None,
    system_prompt=None,
):
    api_key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)
    if not api_key:
        return {
            "success": False,
            "body": None,
            "error": "OPENAI_API_KEY is not set",
            "model": model,
            "response_id": None,
            "token_usage": {},
        }

    prompt = (
        "Write one SMS to follow up after a restaurant guest redeemed a promo code.\n"
        f"Promo code: {(promo_code or {}).get('code') or ''}\n"
        f"Customer name: {display_name or ''}\n"
        f"Staff redemption notes from this visit: {redemption_notes or ''}\n"
        f"Last order notes: {last_order_notes or ''}\n"
        f"Customer profile summary: {profile_summary or ''}\n\n"
        "Requirements: keep it friendly and natural, ask how their experience was, "
        "use this visit's staff notes if present, do not sound robotic, no markdown, "
        "and stay under 320 characters when possible."
    )

    client = OpenAI(api_key=api_key)
    try:
        response = client.responses.create(
            model=model,
            instructions=_build_instructions(system_prompt, FOLLOWUP_SMS_SYSTEM_PROMPT),
            input=[{"role": "user", "content": prompt}],
        )
        body = _compact_text(response.output_text, MAX_FOLLOWUP_SMS_CHARS)
        if not body:
            raise ValueError("Follow-up SMS response did not contain text")
        return {
            "success": True,
            "body": body,
            "error": None,
            "model": model,
            "response_id": getattr(response, "id", None),
            "token_usage": _serialize_usage(response),
        }
    except Exception as exc:
        return {
            "success": False,
            "body": None,
            "error": str(exc),
            "model": model,
            "response_id": None,
            "token_usage": {},
        }


def extract_lead_contact_info(history):
    api_key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)
    if not api_key:
        return {
            "success": False,
            "customer_name": None,
            "phone": None,
            "error": "OPENAI_API_KEY is not set",
            "model": model,
            "response_id": None,
            "token_usage": {},
        }

    client = OpenAI(api_key=api_key)
    trimmed_history = _trim_history(history)
    extraction_input = [
        *trimmed_history,
        {
            "role": "user",
            "content": (
                "From the conversation above, extract the customer's name and phone number. "
                "Return compact JSON only, for example: "
                '{"customer_name":"Alex Kim","phone":"6045551212"}.'
            ),
        },
    ]

    try:
        response = client.responses.create(
            model=model,
            instructions=LEAD_EXTRACTION_SYSTEM_PROMPT,
            input=extraction_input,
        )
        raw_text = (response.output_text or "").strip()
        parsed = json.loads(raw_text)
        return {
            "success": True,
            "customer_name": parsed.get("customer_name") if isinstance(parsed, dict) else None,
            "phone": parsed.get("phone") if isinstance(parsed, dict) else None,
            "error": None,
            "model": model,
            "response_id": getattr(response, "id", None),
            "token_usage": _serialize_usage(response),
        }
    except Exception as exc:
        return {
            "success": False,
            "customer_name": None,
            "phone": None,
            "error": str(exc),
            "model": model,
            "response_id": None,
            "token_usage": {},
        }
