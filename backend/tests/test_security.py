from __future__ import annotations

import hashlib
import hmac

from christa_ig.security import secure_equals, verify_meta_signature


def test_meta_signature_accepts_valid_digest():
    body = b'{"object":"instagram"}'
    digest = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert verify_meta_signature(body, f"sha256={digest}", "secret") is True


def test_meta_signature_rejects_missing_malformed_and_wrong_values():
    assert verify_meta_signature(b"payload", None, "secret") is False
    assert verify_meta_signature(b"payload", "sha1=bad", "secret") is False
    assert verify_meta_signature(b"payload", "sha256=bad", "secret") is False
    assert verify_meta_signature(b"", "sha256=bad", "secret") is False


def test_secure_equals_requires_nonempty_matching_values():
    assert secure_equals("same", "same") is True
    assert secure_equals("same", "different") is False
    assert secure_equals("", "") is False
