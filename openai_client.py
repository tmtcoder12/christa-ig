import os

from openai import OpenAI


DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant responding to Instagram direct messages."
DEFAULT_FALLBACK_MESSAGE = "Thanks for your message — I'll get back to you shortly."
MAX_HISTORY_MESSAGES = 20


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


def generate_reply(history, system_prompt=None):
    api_key = os.environ.get("OPENAI_API_KEY")
    fallback_message = os.environ.get("OPENAI_FALLBACK_MESSAGE", DEFAULT_FALLBACK_MESSAGE)

    if not api_key:
        return {
            "success": False,
            "reply_text": fallback_message,
            "error": "OPENAI_API_KEY is not set",
            "used_fallback": True,
            "model": os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
            "response_id": None,
            "token_usage": {},
        }

    client = OpenAI(api_key=api_key)
    model = os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)
    system_prompt = system_prompt or os.environ.get("OPENAI_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT)
    trimmed_history = _trim_history(history)

    try:
        response = client.responses.create(
            model=model,
            instructions=system_prompt,
            input=trimmed_history,
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
            }

        return {
            "success": False,
            "reply_text": fallback_message,
            "error": "OpenAI response did not contain text",
            "used_fallback": True,
            "model": model,
            "response_id": getattr(response, "id", None),
            "token_usage": _serialize_usage(response),
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
        }
