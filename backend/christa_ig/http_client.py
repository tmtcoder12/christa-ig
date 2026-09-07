"""Small, explicit HTTP transport shared by service integrations."""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_TIMEOUT_SECONDS = 10
SAFE_RETRY_DELAYS_SECONDS = (0.25, 0.75)
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


def perform_request(request, *, timeout=DEFAULT_TIMEOUT_SECONDS, retry_safe=False) -> HttpResponse:
    """Run one request, retrying only explicitly safe, idempotent operations."""
    method = request.get_method().upper()
    if retry_safe and method not in {"GET", "HEAD"}:
        raise ValueError("Retries are only permitted for safe GET or HEAD requests")

    attempts = len(SAFE_RETRY_DELAYS_SECONDS) + 1 if retry_safe else 1
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return HttpResponse(status=response.status, body=response.read())
        except urllib.error.HTTPError as exc:
            should_retry = retry_safe and exc.code in RETRYABLE_STATUS_CODES and attempt < attempts - 1
            if not should_retry:
                raise
            exc.read()
            exc.close()
        except (urllib.error.URLError, TimeoutError):
            if not retry_safe or attempt >= attempts - 1:
                raise
        time.sleep(SAFE_RETRY_DELAYS_SECONDS[attempt])

    raise RuntimeError("HTTP retry loop ended unexpectedly")  # pragma: no cover
