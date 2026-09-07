"""Flask application factory."""

from __future__ import annotations

import workflows
from flask import Flask
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import get_settings
from .observability import begin_request, configure_logging
from .routes import register_routes


def create_app() -> Flask:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = settings.max_content_length
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.before_request(begin_request)
    app.after_request(workflows.add_api_cors_headers)
    app.register_error_handler(500, workflows._handle_unexpected_error)
    app.register_error_handler(413, workflows._handle_request_too_large)
    app.register_error_handler(HTTPException, workflows._handle_http_error)
    register_routes(app)
    return app
