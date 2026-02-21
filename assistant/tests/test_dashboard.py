"""Tests for dashboard config store."""

import pytest

from assistant.dashboard.config_store import (
    REDIS_PREFIX,
    set_config_in_redis_sync,
    get_config_from_redis_sync,
)


def test_config_store_roundtrip():
    try:
        import redis
        r = redis.from_url("redis://localhost:6379/13", decode_responses=True)
        r.ping()
        r.close()
    except Exception:
        pytest.skip("Redis not available")
    url = "redis://localhost:6379/13"
    set_config_in_redis_sync(url, "TEST_KEY", "test_value")
    data = get_config_from_redis_sync(url)
    assert data.get("TEST_KEY") == "test_value"
    set_config_in_redis_sync(url, "TEST_KEY", "")
    data2 = get_config_from_redis_sync(url)
    assert data2.get("TEST_KEY") == ""
