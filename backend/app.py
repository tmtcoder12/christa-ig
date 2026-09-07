"""Compatible WSGI entrypoint for Gunicorn and local development."""

from __future__ import annotations

import os

from christa_ig.factory import create_app

app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
