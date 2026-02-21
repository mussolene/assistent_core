"""MCP endpoint'ы: URL + секрет для доступа агента по HTTP/SSE. Хранение в Redis."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import uuid

logger = logging.getLogger(__name__)

MCP_ENDPOINTS_SET = "assistant:mcp_endpoints"
MCP_ENDPOINT_PREFIX = "assistant:mcp_endpoint:"
MCP_ENDPOINT_BY_CHAT_PREFIX = "assistant:mcp_endpoint_by_chat:"
MCP_EVENT_QUEUE_PREFIX = "assistant:mcp_event_queue:"
MCP_EVENT_QUEUE_TTL = 3600  # 1h


def _redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def create_endpoint(name: str, chat_id: str) -> tuple[str, str]:
    """
    Создать MCP endpoint. Возвращает (endpoint_id, secret).
    Secret показывается один раз — сохраните его для подстановки в MCP config.
    """
    import redis
    endpoint_id = str(uuid.uuid4()).replace("-", "")[:16]
    secret = secrets.token_urlsafe(32)
    secret_hash = _hash_secret(secret)
    r = redis.from_url(_redis_url(), decode_responses=True)
    try:
        r.sadd(MCP_ENDPOINTS_SET, endpoint_id)
        r.set(
            MCP_ENDPOINT_PREFIX + endpoint_id,
            json.dumps({
                "name": name,
                "chat_id": chat_id,
                "secret_hash": secret_hash,
                "created_at": "",
            }),
        )
        r.set(MCP_ENDPOINT_BY_CHAT_PREFIX + chat_id, endpoint_id)
        return endpoint_id, secret
    finally:
        r.close()


def list_endpoints() -> list[dict]:
    """Список endpoint'ов (без секрета): id, name, chat_id, created_at."""
    import redis
    r = redis.from_url(_redis_url(), decode_responses=True)
    try:
        ids = r.smembers(MCP_ENDPOINTS_SET) or []
        out = []
        for eid in ids:
            raw = r.get(MCP_ENDPOINT_PREFIX + eid)
            if not raw:
                continue
            try:
                data = json.loads(raw)
                out.append({
                    "id": eid,
                    "name": data.get("name", ""),
                    "chat_id": data.get("chat_id", ""),
                    "created_at": data.get("created_at", ""),
                })
            except json.JSONDecodeError:
                continue
        return out
    finally:
        r.close()


def get_endpoint(endpoint_id: str) -> dict | None:
    """Получить endpoint по id (без секрета)."""
    import redis
    r = redis.from_url(_redis_url(), decode_responses=True)
    try:
        raw = r.get(MCP_ENDPOINT_PREFIX + endpoint_id)
        if not raw:
            return None
        data = json.loads(raw)
        data["id"] = endpoint_id
        return data
    except (json.JSONDecodeError, TypeError):
        return None
    finally:
        r.close()


def verify_endpoint_secret(endpoint_id: str, secret: str) -> bool:
    """Проверить Bearer secret для endpoint_id."""
    ep = get_endpoint(endpoint_id)
    if not ep:
        return False
    stored_hash = ep.get("secret_hash")
    if not stored_hash:
        return False
    return secrets.compare_digest(_hash_secret(secret), stored_hash)


def get_chat_id_for_endpoint(endpoint_id: str) -> str | None:
    ep = get_endpoint(endpoint_id)
    return ep.get("chat_id") if ep else None


def get_endpoint_id_for_chat(chat_id: str) -> str | None:
    """По chat_id (Telegram) получить endpoint_id для публикации событий."""
    import redis
    r = redis.from_url(_redis_url(), decode_responses=True)
    try:
        return r.get(MCP_ENDPOINT_BY_CHAT_PREFIX + chat_id)
    finally:
        r.close()


def delete_endpoint(endpoint_id: str) -> bool:
    import redis
    r = redis.from_url(_redis_url(), decode_responses=True)
    try:
        ep = get_endpoint(endpoint_id)
        if not ep:
            return False
        chat_id = ep.get("chat_id")
        r.srem(MCP_ENDPOINTS_SET, endpoint_id)
        r.delete(MCP_ENDPOINT_PREFIX + endpoint_id)
        if chat_id:
            r.delete(MCP_ENDPOINT_BY_CHAT_PREFIX + chat_id)
        r.delete(MCP_EVENT_QUEUE_PREFIX + endpoint_id)
        return True
    finally:
        r.close()


def regenerate_endpoint_secret(endpoint_id: str) -> str | None:
    """Новый секрет для endpoint. Возвращает plain secret или None."""
    import redis
    ep = get_endpoint(endpoint_id)
    if not ep:
        return None
    secret = secrets.token_urlsafe(32)
    secret_hash = _hash_secret(secret)
    r = redis.from_url(_redis_url(), decode_responses=True)
    try:
        data = json.loads(r.get(MCP_ENDPOINT_PREFIX + endpoint_id) or "{}")
        data["secret_hash"] = secret_hash
        r.set(MCP_ENDPOINT_PREFIX + endpoint_id, json.dumps(data))
        return secret
    finally:
        r.close()


def push_mcp_event(endpoint_id: str, event_type: str, data: dict) -> None:
    """Положить событие в очередь для SSE (Redis list)."""
    import redis
    r = redis.from_url(_redis_url(), decode_responses=True)
    try:
        key = MCP_EVENT_QUEUE_PREFIX + endpoint_id
        payload = json.dumps({"type": event_type, "data": data})
        r.rpush(key, payload)
        r.expire(key, MCP_EVENT_QUEUE_TTL)
    except Exception as e:
        logger.exception("push_mcp_event: %s", e)
    finally:
        r.close()


def pop_mcp_events(endpoint_id: str, timeout_sec: float = 30.0) -> list[dict]:
    """Забрать события из очереди (для SSE). BLPOP с timeout."""
    import redis
    r = redis.from_url(_redis_url(), decode_responses=True)
    key = MCP_EVENT_QUEUE_PREFIX + endpoint_id
    try:
        # BLPOP key timeout -> (key, value) or None
        raw = r.blpop(key, timeout=int(timeout_sec))
        if not raw:
            return []
        _, payload = raw
        try:
            return [json.loads(payload)]
        except json.JSONDecodeError:
            return []
    finally:
        r.close()
