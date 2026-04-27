import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

from flask import Flask, jsonify, request


app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
INSTAGRAM_SEND_MESSAGE_URL = "https://graph.instagram.com/v24.0/me/messages"


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


def extract_dm_sender_id(payload):
    if not isinstance(payload, dict):
        return None

    entries = payload.get("entry")
    if not isinstance(entries, list) or not entries:
        return None

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        messaging_items = entry.get("messaging")
        if not isinstance(messaging_items, list):
            continue

        for item in messaging_items:
            if not isinstance(item, dict):
                continue

            sender = item.get("sender")
            message = item.get("message")
            if isinstance(sender, dict) and isinstance(message, dict):
                sender_id = sender.get("id")
                if sender_id:
                    return sender_id

    return None


def extract_inbound_dm_sender_id(payload):
    if not isinstance(payload, dict):
        return None

    entries = payload.get("entry")
    if not isinstance(entries, list) or not entries:
        return None

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        account_id = entry.get("id")
        messaging_items = entry.get("messaging")
        if not isinstance(messaging_items, list):
            continue

        for item in messaging_items:
            if not isinstance(item, dict):
                continue

            sender = item.get("sender")
            message = item.get("message")
            if not isinstance(sender, dict) or not isinstance(message, dict):
                continue

            sender_id = sender.get("id")
            message_text = message.get("text")
            if not sender_id or not message_text:
                continue

            if sender_id == account_id:
                continue

            return sender_id

    return None


def send_instagram_dm(recipient_id, text):
    access_token = os.environ.get("INSTAGRAM_ACCESS_TOKEN")

    if not access_token:
        return {
            "success": False,
            "error": "INSTAGRAM_ACCESS_TOKEN is not set",
        }

    body = json.dumps(
        {
            "recipient": {"id": recipient_id},
            "message": {"text": text},
        }
    ).encode("utf-8")
    api_request = urllib.request.Request(
        INSTAGRAM_SEND_MESSAGE_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(api_request, timeout=10) as response:
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
    send_message_response = None

    if event_type == "dm-related":
        sender_id = extract_inbound_dm_sender_id(payload)
        if sender_id:
            send_message_response = send_instagram_dm(sender_id, "Testing !")
        else:
            send_message_response = {
                "success": False,
                "error": "No inbound dm sender found in dm-related payload",
            }

    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": request.method,
        "path": request.path,
        "event_type": event_type,
        "headers": dict(request.headers),
        "query_params": request.args.to_dict(flat=False),
        "json": payload,
        "raw_body": body_text,
        "send_message_response": send_message_response,
    }

    logger.info("Webhook received:\n%s", json.dumps(log_entry, indent=2, default=str))

    return "OK", 200, {"Content-Type": "text/plain; charset=utf-8"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
