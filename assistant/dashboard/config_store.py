"""Read/write config from Redis. Used by dashboard and by telegram adapter when env is empty."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

REDIS_PREFIX = "assistant:config:"


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
            name = k[len(REDIS_PREFIX):]
            val = await client.get(k)
            if val is not None:
                if name == "TELEGRAM_ALLOWED_USER_IDS" and val:
                    try:
                        out[name] = [int(x.strip()) for x in val.split(",") if x.strip()]
                    except ValueError:
                        out[name] = val
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
            name = k[len(REDIS_PREFIX):]
            val = client.get(k)
            if val is not None:
                if name == "TELEGRAM_ALLOWED_USER_IDS" and val:
                    try:
                        out[name] = [int(x.strip()) for x in val.split(",") if x.strip()]
                    except ValueError:
                        out[name] = val
                else:
                    out[name] = val
        client.close()
        return out
    except Exception as e:
        logger.warning("Could not load config from Redis: %s", e)
        return {}


async def set_config_in_redis(redis_url: str, key: str, value: str | list[int]) -> None:
    if isinstance(value, list):
        value = ",".join(str(x) for x in value)
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(redis_url, decode_responses=True)
        await client.set(REDIS_PREFIX + key, value)
        await client.close()
    except Exception as e:
        logger.exception("Could not save config to Redis: %s", e)
        raise


def set_config_in_redis_sync(redis_url: str, key: str, value: str | list[int]) -> None:
    if isinstance(value, list):
        value = ",".join(str(x) for x in value)
    try:
        import redis
        client = redis.from_url(redis_url, decode_responses=True)
        client.set(REDIS_PREFIX + key, value)
        client.close()
    except Exception as e:
        logger.exception("Could not save config to Redis: %s", e)
        raise
