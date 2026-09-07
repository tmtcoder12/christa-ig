"""Normalize Meta webhook deliveries into independently retryable jobs."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _fallback_id(event_type: str, payload: dict[str, Any], index: int) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(f"{index}:{canonical}".encode("utf-8")).hexdigest()
    return f"generated:{event_type}:{digest}"


def expand_meta_events(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("entry"), list):
        return []

    jobs: list[dict[str, Any]] = []
    sequence = 0
    for entry in payload["entry"]:
        if not isinstance(entry, dict):
            continue
        base = {key: value for key, value in entry.items() if key not in {"messaging", "changes"}}

        for item in entry.get("messaging") or []:
            if not isinstance(item, dict):
                continue
            sequence += 1
            normalized = {"object": payload.get("object"), "entry": [{**base, "messaging": [item]}]}
            message = item.get("message") if isinstance(item.get("message"), dict) else {}
            external_id = message.get("mid") or _fallback_id("dm-related", normalized, sequence)
            jobs.append(
                {
                    "provider": "meta",
                    "external_event_id": f"dm:{external_id}",
                    "event_type": "dm-related",
                    "account_external_id": str(entry.get("id") or ""),
                    "payload": normalized,
                }
            )

        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            sequence += 1
            normalized = {"object": payload.get("object"), "entry": [{**base, "changes": [change]}]}
            value = change.get("value") if isinstance(change.get("value"), dict) else {}
            external_id = (
                value.get("id") or value.get("comment_id") or _fallback_id("comment-related", normalized, sequence)
            )
            jobs.append(
                {
                    "provider": "meta",
                    "external_event_id": f"comment:{external_id}",
                    "event_type": "comment-related",
                    "account_external_id": str(entry.get("id") or ""),
                    "payload": normalized,
                }
            )
    return jobs


RETRY_DELAYS_SECONDS = (2, 10, 30, 120, 300)


def retry_delay(attempt_count: int) -> int:
    index = max(0, min(attempt_count - 1, len(RETRY_DELAYS_SECONDS) - 1))
    return RETRY_DELAYS_SECONDS[index]
