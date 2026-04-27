import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from flask import Flask, jsonify, request


app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
INSTAGRAM_MEDIA_URL = "https://graph.instagram.com/v24.0/17841476354816630/media"


def classify_meta_event(payload):
    if not isinstance(payload, dict):
        return "unknown"

    entries = payload.get("entry")
    if not isinstance(entries, list):
        return "unknown"

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        if entry.get("changes"):
            return "comment-related"

        messaging = entry.get("messaging")
        if isinstance(messaging, list) and messaging:
            return "dm-related"

    return "unknown"


def fetch_instagram_media():
    access_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN")

    if not access_token:
        return {
            "success": False,
            "error": "INSTAGRAM_ACCESS_TOKEN is not set",
        }

    query = urllib.parse.urlencode({"access_token": access_token})
    request_url = f"{INSTAGRAM_MEDIA_URL}?{query}"

    try:
        with urllib.request.urlopen(request_url, timeout=10) as response:
            body = response.read().decode("utf-8")
            parsed_body = json.loads(body)
            return {
                "success": True,
                "status_code": response.status,
                "data": parsed_body,
            }
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        return {
            "success": False,
            "status_code": exc.code,
            "error": error_body,
        }
    except urllib.error.URLError as exc:
        return {
            "success": False,
            "error": str(exc.reason),
        }
    except json.JSONDecodeError as exc:
        return {
            "success": False,
            "error": f"Invalid JSON response: {exc}",
        }


@app.get("/")
def healthcheck():
    return jsonify({"status": "ok"})


@app.get("/webhook")
def verify_webhook():
    mode = request.args.get("hub.mode")
    verify_token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge", "")
    expected_token = os.environ.get("META_VERIFY_TOKEN")

    if mode == "subscribe" and expected_token and verify_token == expected_token:
        logger.info("Meta webhook verification succeeded for path %s", request.path)
        return challenge, 200, {"Content-Type": "text/plain; charset=utf-8"}

    logger.warning(
        "Meta webhook verification failed: mode=%s token_provided=%s expected_token_set=%s",
        mode,
        bool(verify_token),
        bool(expected_token),
    )
    return "Forbidden", 403


@app.post("/webhook")
def webhook():
    payload = request.get_json(silent=True)
    body_text = request.get_data(as_text=True)
    event_type = classify_meta_event(payload)
    instagram_response = fetch_instagram_media()

    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": request.method,
        "path": request.path,
        "event_type": event_type,
        "headers": dict(request.headers),
        "query_params": request.args.to_dict(flat=False),
        "json": payload,
        "raw_body": body_text,
        "instagram_media_response": instagram_response,
    }

    logger.info("Webhook received:\n%s", json.dumps(log_entry, indent=2, default=str))

    return "OK", 200, {"Content-Type": "text/plain; charset=utf-8"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
