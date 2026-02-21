"""Read/write config from Redis. Used by dashboard and by telegram adapter when env is empty."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

REDIS_PREFIX = "assistant:config:"
MCP_SERVERS_KEY = "MCP_SERVERS"
PAIRING_MODE_KEY = "PAIRING_MODE"
PAIRING_CODE_PREFIX = "assistant:pairing:"
PAIRING_CODE_TTL = 600  # 10 min


def get_redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")


async def get_config_from_redis(redis_url: str) -> dict[str, Any]:
    """Load config keys from Redis. Returns dict of key -> value (strings)."""
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(redis_url, decode_responses=True)
        await client.ping()
        keys = await client.keys(REDIS_PREFIX + "*")
        out = {}
        for k in keys:
            name = k[len(REDIS_PREFIX) :]
            val = await client.get(k)
            if val is not None:
                if name == "TELEGRAM_ALLOWED_USER_IDS" and val:
                    try:
                        out[name] = [int(x.strip()) for x in val.split(",") if x.strip()]
                    except ValueError:
                        out[name] = val
                elif name == MCP_SERVERS_KEY:
                    try:
                        out[name] = json.loads(val) if val else []
                    except json.JSONDecodeError:
                        out[name] = []
                else:
                    out[name] = val
        await client.close()
        return out
    except Exception as e:
        logger.warning("Could not load config from Redis: %s", e)
        return {}


def get_config_from_redis_sync(redis_url: str) -> dict[str, Any]:
    """Sync version for use in non-async contexts."""
    try:
        import redis

        client = redis.from_url(redis_url, decode_responses=True)
        client.ping()
        keys = client.keys(REDIS_PREFIX + "*")
        out = {}
        for k in keys:
            name = k[len(REDIS_PREFIX) :]
            val = client.get(k)
            if val is not None:
                if name == "TELEGRAM_ALLOWED_USER_IDS" and val:
                    try:
                        out[name] = [int(x.strip()) for x in val.split(",") if x.strip()]
                    except ValueError:
                        out[name] = val
                elif name == MCP_SERVERS_KEY:
                    try:
                        out[name] = json.loads(val) if val else []
                    except json.JSONDecodeError:
                        out[name] = []
                else:
                    out[name] = val
        client.close()
        return out
    except Exception as e:
        logger.warning("Could not load config from Redis: %s", e)
        return {}


def _serialize_value(key: str, value: Any) -> str:
    if key == MCP_SERVERS_KEY:
        return json.dumps(value) if not isinstance(value, str) else value
    if isinstance(value, list):
        return ",".join(str(x) for x in value)
    return str(value)


async def set_config_in_redis(
    redis_url: str, key: str, value: str | list[int] | list[dict]
) -> None:
    val_str = _serialize_value(key, value)
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(redis_url, decode_responses=True)
        await client.set(REDIS_PREFIX + key, val_str)
        await client.close()
    except Exception as e:
        logger.exception("Could not save config to Redis: %s", e)
        raise


async def add_telegram_allowed_user(redis_url: str, user_id: int) -> None:
    """Append user_id to TELEGRAM_ALLOWED_USER_IDS in Redis."""
    cfg = await get_config_from_redis(redis_url)
    current = cfg.get("TELEGRAM_ALLOWED_USER_IDS") or []
    if not isinstance(current, list):
        current = [int(x.strip()) for x in str(current).split(",") if x.strip()]
    if user_id in current:
        return
    current = list(current) + [user_id]
    await set_config_in_redis(redis_url, "TELEGRAM_ALLOWED_USER_IDS", current)


def create_pairing_code(redis_url: str) -> tuple[str, int]:
    """Create one-time pairing code. Returns (code, expires_in_sec). Code is 6 alphanumeric."""
    import secrets
    import string

    code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
    try:
        import redis

        client = redis.from_url(redis_url, decode_responses=True)
        key = PAIRING_CODE_PREFIX + code
        client.setex(key, PAIRING_CODE_TTL, "1")
        client.close()
        return code, PAIRING_CODE_TTL
    except Exception as e:
        logger.exception("Could not create pairing code: %s", e)
        raise


def consume_pairing_code(redis_url: str, code: str) -> bool:
    """Use pairing code (one-time). Returns True if code was valid and consumed."""
    if not code or len(code) > 32:
        return False
    code = code.strip().upper()
    try:
        import redis

        client = redis.from_url(redis_url, decode_responses=True)
        key = PAIRING_CODE_PREFIX + code
        if client.delete(key):
            client.close()
            return True
        client.close()
        return False
    except Exception:
        return False


def set_config_in_redis_sync(redis_url: str, key: str, value: str | list[int] | list[dict]) -> None:
    val_str = _serialize_value(key, value)
    try:
        import redis

        client = redis.from_url(redis_url, decode_responses=True)
        client.set(REDIS_PREFIX + key, val_str)
        client.close()
    except Exception as e:
        logger.exception("Could not save config to Redis: %s", e)
        raise
