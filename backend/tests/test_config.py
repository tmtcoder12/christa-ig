from __future__ import annotations

import pytest
from christa_ig import config


def test_frontend_origins_support_new_and_legacy_names(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("FRONTEND_ORIGINS", "https://one.example/, https://two.example")
    settings = config.Settings.from_env()
    assert "https://one.example" in settings.frontend_origins
    assert "https://two.example" in settings.frontend_origins
    assert "http://localhost:5173" in settings.frontend_origins


def test_production_configuration_fails_fast(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *_args, **_kwargs: None)
    for name in (
        "META_VERIFY_TOKEN",
        "META_APP_SECRET",
        "INSTAGRAM_ACCESS_TOKEN",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_ANON_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="Missing required production settings"):
        config.Settings.from_env()


def test_production_cors_does_not_implicitly_allow_localhost(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("FRONTEND_ORIGINS", "https://staff.example.com/")
    for name in (
        "META_VERIFY_TOKEN",
        "META_APP_SECRET",
        "INSTAGRAM_ACCESS_TOKEN",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_ANON_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.setenv(name, "configured")
    settings = config.Settings.from_env()
    assert settings.frontend_origins == ("https://staff.example.com",)


def test_invalid_positive_integer_is_rejected(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("WORKER_BATCH_SIZE", "0")
    with pytest.raises(RuntimeError, match="greater than zero"):
        config.Settings.from_env()


def test_partial_twilio_configuration_is_rejected(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "configured")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWILIO_MESSAGING_SERVICE_SID", raising=False)
    with pytest.raises(RuntimeError, match="Twilio configuration"):
        config.Settings.from_env()
