"""Tests for config loading."""



from assistant.config.loader import Config, _load_yaml


def test_load_yaml_missing(tmp_path):
    assert _load_yaml(tmp_path / "nonexistent.yaml") == {}


def test_load_yaml_exists(tmp_path):
    path = tmp_path / "test.yaml"
    path.write_text("redis:\n  url: redis://custom:6380/2\n")
    data = _load_yaml(path)
    assert data["redis"]["url"] == "redis://custom:6380/2"


def test_config_load_from_path(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("model:\n  provider: local\n  name: test\nredis:\n  url: redis://localhost:6379/0\n")
    config = Config.load(config_path=path)
    assert config.model.provider == "local"
    assert config.model.name == "test"


def test_config_env_override(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://test:6379/5")
    config = Config.load()
    assert config.redis.url == "redis://test:6379/5"
