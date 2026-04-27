import json
import logging
import os
from datetime import datetime, timezone

from flask import Flask, jsonify, request


app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@app.get("/")
def healthcheck():
    return jsonify({"status": "ok"})


@app.post("/webhook")
def webhook():
    payload = request.get_json(silent=True)
    body_text = request.get_data(as_text=True)

    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": request.method,
        "path": request.path,
        "headers": dict(request.headers),
        "query_params": request.args.to_dict(flat=False),
        "json": payload,
        "raw_body": body_text,
    }

    logger.info("Webhook received:\n%s", json.dumps(log_entry, indent=2, default=str))

    return jsonify(
        {
            "success": True,
            "message": "Webhook received. Check Render logs for console output.",
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
