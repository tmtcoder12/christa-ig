import json
import os
import sys
import urllib.error
import urllib.request

from christa_ig.http_client import perform_request


def required_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def main():
    backend_url = required_env("BACKEND_URL").rstrip("/")
    cron_secret = required_env("FOLLOWUP_CRON_SECRET")
    request = urllib.request.Request(
        f"{backend_url}/api/followups/process-due",
        headers={"X-Followup-Cron-Secret": cron_secret},
        method="POST",
    )

    try:
        response = perform_request(request, timeout=30)
        body = response.body.decode("utf-8")
        print(body)
        data = json.loads(body)
        return 0 if response.status < 400 and "processed" in data else 1
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(body or str(exc), file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
