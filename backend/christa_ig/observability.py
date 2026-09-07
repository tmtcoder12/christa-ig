"""Request correlation and privacy-safe structured logging."""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from flask import g, has_request_context, request

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
SENSITIVE_KEYS = {
    "authorization",
    "access_token",
    "token",
    "api_key",
    "apikey",
    "secret",
    "phone",
    "phone_e164",
    "phone_raw",
    "body",
    "text",
    "message",
    "content",
    "payload",
    "raw_payload",
}


def configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level, logging.INFO), format="%(message)s", force=True)


def begin_request() -> None:
    supplied = request.headers.get("X-Request-ID", "")
    g.request_id = supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else str(uuid.uuid4())


def request_id() -> str:
    return getattr(g, "request_id", "") or str(uuid.uuid4())


def redact(value: Any, key: str | None = None) -> Any:
    if key and key.lower() in SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): redact(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "request_id": getattr(g, "request_id", None) if has_request_context() else fields.pop("request_id", None),
        **redact(fields),
    }
    logger.log(level, json.dumps(record, separators=(",", ":"), default=str))
