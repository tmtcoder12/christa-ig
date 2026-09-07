"""Flask route registration kept separate from workflow implementation."""

from __future__ import annotations

import workflows


def register_routes(app) -> None:
    app.add_url_rule("/", "healthcheck", workflows.healthcheck, methods=["GET"])
    app.add_url_rule("/health/live", "health_live", workflows.health_live, methods=["GET"])
    app.add_url_rule("/health/ready", "health_ready", workflows.health_ready, methods=["GET"])
    app.add_url_rule("/webhook", "verify_webhook", workflows.verify_webhook, methods=["GET"])
    app.add_url_rule("/webhook", "webhook", workflows.webhook, methods=["POST"])
    app.add_url_rule(
        "/api/twilio/sms-webhook",
        "twilio_sms_webhook",
        workflows.twilio_sms_webhook,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/knowledge-chunks",
        "get_knowledge_chunks_api",
        workflows.get_knowledge_chunks_api,
        methods=["GET"],
    )
    app.add_url_rule(
        "/api/knowledge-chunks",
        "create_knowledge_chunk_api",
        workflows.create_knowledge_chunk_api,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/followups/process-due",
        "process_due_followups_api",
        workflows.process_due_followups_api,
        methods=["POST"],
    )
    app.add_url_rule(
        "/api/promo-codes/redeem",
        "redeem_promo_code_api",
        workflows.redeem_promo_code_api,
        methods=["POST"],
    )
    app.add_url_rule("/api/promotions", "create_promotion", workflows.create_promotion, methods=["POST"])
    app.add_url_rule(
        "/api/promotions/<setup_id>",
        "get_promotion",
        workflows.get_promotion,
        methods=["GET"],
    )
    for index, rule in enumerate(
        (
            "/api/promotions",
            "/api/promotions/<setup_id>",
            "/api/promo-codes/redeem",
            "/api/knowledge-chunks",
            "/api/followups/process-due",
        )
    ):
        app.add_url_rule(
            rule,
            f"api_options_{index}",
            workflows.promotion_api_options,
            methods=["OPTIONS"],
        )
