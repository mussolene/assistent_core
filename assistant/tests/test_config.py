"""Tests for config loading."""

from __future__ import annotations

from assistant.config.loader import Config, _deep_merge, _load_yaml, get_config


def test_load_yaml_missing(tmp_path):
    assert _load_yaml(tmp_path / "nonexistent.yaml") == {}


def test_load_yaml_exists(tmp_path):
    path = tmp_path / "test.yaml"
    path.write_text("redis:\n  url: redis://custom:6380/2\n")
    data = _load_yaml(path)
    assert data["redis"]["url"] == "redis://custom:6380/2"


def test_config_load_from_path(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "model:\n  provider: local\n  name: test\nredis:\n  url: redis://localhost:6379/0\n"
    )
    config = Config.load(config_path=path)
    assert config.model.provider == "local"
    assert config.model.name == "test"


def test_config_env_override(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://test:6379/5")
    config = Config.load()
    assert config.redis.url == "redis://test:6379/5"


def test_deep_merge():
    base = {"a": 1, "b": {"x": 10, "y": 20}}
    override = {"b": {"y": 22, "z": 30}, "c": 3}
    out = _deep_merge(base, override)
    assert out == {"a": 1, "b": {"x": 10, "y": 22, "z": 30}, "c": 3}
    assert base["b"] == {"x": 10, "y": 20}


def test_config_load_telegram_env(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-token")
    config = Config.load()
    assert config.telegram.bot_token == "secret-token"


def test_config_load_telegram_allowed_user_ids_from_yaml(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(
        "redis:\n  url: redis://localhost:6379/0\ntelegram:\n  allowed_user_ids: [123, 456]\n"
        "security:\n  allowed_user_ids: [123, 456]\n"
    )
    config = Config.load(config_path=path)
    assert config.telegram.allowed_user_ids == [123, 456]
    assert config.security.allowed_user_ids == [123, 456]


def test_config_load_cloud_fallback(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CLOUD_FALLBACK_ENABLED", "true")
    config = Config.load()
    assert config.model.cloud_fallback_enabled is True
    assert config.security.cloud_fallback_enabled is True


def test_config_load_env_prefix_missing_file(monkeypatch, tmp_path):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("ASSISTANT_ENV_PREFIX", "staging")
    monkeypatch.chdir(tmp_path)
    config = Config.load()
    assert config.redis.url == "redis://localhost:6379/0"


def test_get_config(monkeypatch, tmp_path):
    """get_config(config_path=...) loads from file; redis url can be overridden by REDIS_URL."""
    monkeypatch.setenv("REDIS_URL", "redis://getconfig:6379/0")
    path = tmp_path / "c.yaml"
    path.write_text("redis:\n  url: redis://getconfig:6379/0\nmodel:\n  name: fromfile\n")
    config = get_config(config_path=str(path))
    assert config.model.name == "fromfile"
    assert config.redis.url == "redis://getconfig:6379/0"
