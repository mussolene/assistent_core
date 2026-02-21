"""Pytest fixtures and config."""


import pytest


@pytest.fixture(autouse=True)
def env_cleanup(monkeypatch):
    """Avoid loading real .env in tests."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/1")
    yield
