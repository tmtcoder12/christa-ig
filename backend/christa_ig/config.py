"""Typed environment configuration for the web and worker processes."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
ENVIRONMENTS = {"development", "test", "production"}


def _as_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_positive_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise RuntimeError(f"{name} must be greater than zero")
    return parsed


def _origins(app_env: str) -> tuple[str, ...]:
    raw = os.getenv("FRONTEND_ORIGINS") or os.getenv("FRONTEND_ORIGIN") or ""
    configured = [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]
    local = [] if app_env == "production" else ["http://127.0.0.1:5173", "http://localhost:5173"]
    return tuple(dict.fromkeys([*local, *configured]))


@dataclass(frozen=True)
class Settings:
    app_env: str
    log_level: str
    frontend_origins: tuple[str, ...]
    max_content_length: int
    meta_verify_token: str
    meta_app_secret: str
    instagram_access_token: str
    supabase_url: str
    supabase_service_role_key: str
    supabase_anon_key: str
    openai_api_key: str
    followup_cron_secret: str
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_messaging_service_sid: str
    twilio_validate_signature: bool
    worker_poll_seconds: int
    worker_batch_size: int
    worker_max_attempts: int
    webhook_failure_retention_days: int
    worker_lease_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(BACKEND_DIR / ".env", override=False)
        app_env = os.getenv("APP_ENV", "development").strip().lower()
        if app_env not in ENVIRONMENTS:
            raise RuntimeError(f"APP_ENV must be one of: {', '.join(sorted(ENVIRONMENTS))}")

        settings = cls(
            app_env=app_env,
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
            frontend_origins=_origins(app_env),
            max_content_length=_as_positive_int("MAX_CONTENT_LENGTH", 1_048_576),
            meta_verify_token=os.getenv("META_VERIFY_TOKEN", "").strip(),
            meta_app_secret=os.getenv("META_APP_SECRET", "").strip(),
            instagram_access_token=os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip(),
            supabase_url=os.getenv("SUPABASE_URL", "").strip().rstrip("/"),
            supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip(),
            supabase_anon_key=os.getenv("SUPABASE_ANON_KEY", "").strip(),
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            followup_cron_secret=os.getenv("FOLLOWUP_CRON_SECRET", "").strip(),
            twilio_account_sid=os.getenv("TWILIO_ACCOUNT_SID", "").strip(),
            twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN", "").strip(),
            twilio_messaging_service_sid=os.getenv("TWILIO_MESSAGING_SERVICE_SID", "").strip(),
            twilio_validate_signature=_as_bool("TWILIO_VALIDATE_SIGNATURE", True),
            worker_poll_seconds=_as_positive_int("WORKER_POLL_SECONDS", 2),
            worker_batch_size=_as_positive_int("WORKER_BATCH_SIZE", 10),
            worker_max_attempts=_as_positive_int("WORKER_MAX_ATTEMPTS", 5),
            webhook_failure_retention_days=_as_positive_int("WEBHOOK_FAILURE_RETENTION_DAYS", 7),
            worker_lease_seconds=_as_positive_int("WORKER_LEASE_SECONDS", 300),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise RuntimeError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        twilio_values = (
            self.twilio_account_sid,
            self.twilio_auth_token,
            self.twilio_messaging_service_sid,
        )
        if any(twilio_values) and not all(twilio_values):
            raise RuntimeError("Twilio configuration must include account SID, auth token, and messaging service SID")
        if self.app_env != "production":
            return
        required = {
            "FRONTEND_ORIGINS": ",".join(self.frontend_origins),
            "META_VERIFY_TOKEN": self.meta_verify_token,
            "META_APP_SECRET": self.meta_app_secret,
            "INSTAGRAM_ACCESS_TOKEN": self.instagram_access_token,
            "SUPABASE_URL": self.supabase_url,
            "SUPABASE_SERVICE_ROLE_KEY": self.supabase_service_role_key,
            "SUPABASE_ANON_KEY": self.supabase_anon_key,
            "OPENAI_API_KEY": self.openai_api_key,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"Missing required production settings: {', '.join(missing)}")

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)

    @property
    def sms_configured(self) -> bool:
        return bool(self.twilio_account_sid and self.twilio_auth_token and self.twilio_messaging_service_sid)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


def reset_settings_cache() -> None:
    """Clear cached configuration for isolated tests."""
    get_settings.cache_clear()
