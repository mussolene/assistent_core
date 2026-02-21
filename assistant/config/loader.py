"""Load configuration from YAML and environment variables. No hardcoded secrets."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Default config lives next to this module
_DEFAULT_CONFIG_PATH = Path(__file__).parent / "default.yaml"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REDIS_", extra="ignore")
    url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")


class TelegramSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TELEGRAM_", extra="ignore")
    enabled: bool = True
    bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    long_poll_timeout: int = 30
    rate_limit_per_user_per_minute: int = 10
    allowed_user_ids: list[int] = Field(default_factory=list)


class ModelSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MODEL_", extra="ignore")
    provider: str = "local"
    name: str = "llama3.2"
    fallback_name: Optional[str] = None
    cloud_fallback_enabled: bool = False
    reasoning_model_suffix: str = ":reasoning"
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: Optional[str] = Field(default=None, alias="OPENAI_BASE_URL")


class SecuritySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SECURITY_", extra="ignore")
    allowed_user_ids: list[int] = Field(default_factory=list)
    cloud_fallback_enabled: bool = False
    network_egress_enabled: bool = False
    command_whitelist: list[str] = Field(
        default_factory=lambda: ["git", "pytest", "ls", "cat", "python", "python3"]
    )


class MemorySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEMORY_", extra="ignore")
    short_term_window: int = 10
    summary_threshold_messages: int = 20
    vector_top_k: int = 5
    vector_collection: str = "assistant_memory"


class OrchestratorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ORCHESTRATOR_", extra="ignore")
    autonomous_mode: bool = False
    max_iterations: int = 5
    quality_threshold: float = 0.8


class SandboxSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SANDBOX_", extra="ignore")
    workspace_dir: str = "/workspace"
    cpu_limit_seconds: int = 30
    memory_limit_mb: int = 256
    network_enabled: bool = False


class Config(BaseSettings):
    """Application config: YAML + env. Secrets from env only."""

    model_config = SettingsConfigDict(env_nested_delimiter="__", extra="ignore")

    redis: RedisSettings = Field(default_factory=RedisSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    model: ModelSettings = Field(default_factory=ModelSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    orchestrator: OrchestratorSettings = Field(default_factory=OrchestratorSettings)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> "Config":
        path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
        yaml_data = _load_yaml(path)
        env_prefix = os.getenv("ASSISTANT_ENV_PREFIX", "")
        if env_prefix:
            yaml_data = _deep_merge(yaml_data, _load_yaml(Path(f"config/{env_prefix}.yaml")))
        redis_url = os.getenv("REDIS_URL")
        if redis_url:
            yaml_data.setdefault("redis", {})["url"] = redis_url
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if token:
            yaml_data.setdefault("telegram", {})["bot_token"] = token
        allowed = os.getenv("TELEGRAM_ALLOWED_USER_IDS")
        if allowed:
            ids = [int(x.strip()) for x in allowed.split(",") if x.strip()]
            yaml_data.setdefault("telegram", {})["allowed_user_ids"] = ids
            yaml_data.setdefault("security", {})["allowed_user_ids"] = ids
        cloud = os.getenv("CLOUD_FALLBACK_ENABLED", "").lower() in ("1", "true", "yes")
        if cloud:
            yaml_data.setdefault("model", {})["cloud_fallback_enabled"] = True
            yaml_data.setdefault("security", {})["cloud_fallback_enabled"] = True
        return cls(**yaml_data)


def get_config(config_path: str | Path | None = None) -> Config:
    return Config.load(config_path)
