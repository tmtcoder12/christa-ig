from __future__ import annotations

import pytest
from christa_ig.config import reset_settings_cache


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("META_APP_SECRET", "test-meta-secret")
    reset_settings_cache()
    yield
    reset_settings_cache()
