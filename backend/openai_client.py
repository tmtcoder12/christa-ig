import os

from openai import OpenAI


DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant responding to Instagram direct messages."
DEFAULT_FALLBACK_MESSAGE = "Thanks for your message — I'll get back to you shortly."
MAX_HISTORY_MESSAGES = 20
MAX_KNOWLEDGE_CHARS_PER_CHUNK = 900


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
