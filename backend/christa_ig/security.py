"""Webhook signature and secret-comparison helpers."""

from __future__ import annotations

import hashlib
import hmac


def verify_meta_signature(raw_body: bytes, signature_header: str | None, app_secret: str) -> bool:
    if not raw_body or not signature_header or not app_secret:
        return False
    algorithm, separator, supplied_digest = signature_header.partition("=")
    if separator != "=" or algorithm.lower() != "sha256" or not supplied_digest:
        return False
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied_digest.lower())


def secure_equals(expected: str, supplied: str | None) -> bool:
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))
