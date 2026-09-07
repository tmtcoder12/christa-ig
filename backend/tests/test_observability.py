from __future__ import annotations

from christa_ig.observability import redact


def test_redaction_removes_nested_secrets_and_pii():
    value = {
        "authorization": "Bearer secret",
        "nested": {"phone_e164": "+15555555555", "message": "private"},
        "safe": "visible",
    }
    result = redact(value)
    assert result["authorization"] == "[REDACTED]"
    assert result["nested"]["phone_e164"] == "[REDACTED]"
    assert result["nested"]["message"] == "[REDACTED]"
    assert result["safe"] == "visible"
