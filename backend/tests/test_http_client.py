from __future__ import annotations

import urllib.error
import urllib.request

import pytest
from christa_ig import http_client


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return b'{"ok":true}'


def test_safe_get_retries_transient_network_errors(monkeypatch):
    attempts = 0

    def fake_open(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise urllib.error.URLError("temporary")
        return FakeResponse()

    monkeypatch.setattr(http_client.urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(http_client.time, "sleep", lambda _delay: None)
    response = http_client.perform_request(urllib.request.Request("https://example.com"), retry_safe=True)
    assert response.status == 200
    assert attempts == 2


def test_mutations_are_never_retried(monkeypatch):
    attempts = 0

    def fake_open(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise urllib.error.URLError("ambiguous delivery")

    monkeypatch.setattr(http_client.urllib.request, "urlopen", fake_open)
    request = urllib.request.Request("https://example.com/messages", data=b"{}", method="POST")
    with pytest.raises(urllib.error.URLError):
        http_client.perform_request(request)
    assert attempts == 1


def test_retry_flag_rejects_mutations():
    request = urllib.request.Request("https://example.com/messages", data=b"{}", method="POST")
    with pytest.raises(ValueError, match="only permitted"):
        http_client.perform_request(request, retry_safe=True)
