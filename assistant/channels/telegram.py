"""Telegram channel: long polling, whitelist, rate limit, publish to Event Bus, subscribe for replies."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional, Set

import httpx
from assistant.core.bus import EventBus, CH_OUTGOING, CH_STREAM
from assistant.core.events import IncomingMessage, OutgoingReply, StreamToken
from assistant.core.logging_config import setup_logging

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot"


def get_config() -> dict:
    from assistant.config import get_config
    c = get_config()
    return {
        "token": c.telegram.bot_token or os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "allowed_ids": set(c.telegram.allowed_user_ids or []),
        "rate_limit_per_minute": c.telegram.rate_limit_per_user_per_minute,
        "poll_timeout": c.telegram.long_poll_timeout,
    }


class RateLimiter:
    """Sliding window: max N requests per user per minute."""

    def __init__(self, max_per_minute: int = 10) -> None:
        self._max = max_per_minute
        self._hits: dict[str, list[float]] = {}

    def allow(self, user_id: str) -> bool:
        now = time.monotonic()
        window_start = now - 60
        if user_id not in self._hits:
            self._hits[user_id] = []
        self._hits[user_id] = [t for t in self._hits[user_id] if t > window_start]
        if len(self._hits[user_id]) >= self._max:
            return False
        self._hits[user_id].append(now)
        return True


def sanitize_text(text: Optional[str], max_len: int = 4000) -> str:
    """Reduce prompt injection risk: truncate and strip control chars."""
    if text is None or not text:
        return ""
    text = "".join(c for c in text if c.isprintable() or c in "\n\t")
    return text[:max_len].strip()


async def run_telegram_adapter() -> None:
    setup_logging()
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    while True:
        cfg = get_config()
        if not cfg["token"]:
            from assistant.dashboard.config_store import get_config_from_redis
            redis_cfg = await get_config_from_redis(redis_url)
            cfg["token"] = redis_cfg.get("TELEGRAM_BOT_TOKEN") or ""
            ids = redis_cfg.get("TELEGRAM_ALLOWED_USER_IDS")
            cfg["allowed_ids"] = set(ids) if isinstance(ids, list) else (set(int(x) for x in str(ids).split(",") if x.strip()) if ids else set())
        token = cfg["token"]
        if not token:
            logger.warning("TELEGRAM_BOT_TOKEN not set. Configure via Web Dashboard: http://localhost:8080 (retry in 60s)")
            await asyncio.sleep(60)
            continue
        break
    allowed: Set[int] = set(cfg["allowed_ids"]) if cfg.get("allowed_ids") else set()
    rate_limit = cfg["rate_limit_per_minute"]
    poll_timeout = cfg["poll_timeout"]
    bus = EventBus(redis_url)
    await bus.connect()
    limiter = RateLimiter(max_per_minute=rate_limit)
    base_url = f"{TELEGRAM_API}{token}"

    async def on_outgoing(payload: OutgoingReply) -> None:
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{base_url}/sendMessage",
                    json={
                        "chat_id": payload.chat_id,
                        "text": payload.text or "(empty)",
                        "reply_to_message_id": int(payload.message_id) if payload.message_id and payload.message_id.isdigit() else None,
                    },
                    timeout=10.0,
                )
        except Exception as e:
            logger.exception("sendMessage failed: %s", e)

    async def on_stream(payload: StreamToken) -> None:
        pass  # MVP: no streaming edits; full reply sent via OutgoingReply

    bus.subscribe_outgoing(on_outgoing)
    bus.subscribe_stream(on_stream)

    async def poll() -> None:
        offset = 0
        while True:
            try:
                async with httpx.AsyncClient() as client:
                    r = await client.get(
                        f"{base_url}/getUpdates",
                        params={"timeout": poll_timeout, "offset": offset},
                        timeout=poll_timeout + 5,
                    )
                    data = r.json()
                if not data.get("ok"):
                    logger.warning("getUpdates not ok: %s", data)
                    await asyncio.sleep(5)
                    continue
                for upd in data.get("result", []):
                    offset = upd["update_id"] + 1
                    msg = upd.get("message") or upd.get("edited_message")
                    if not msg:
                        continue
                    user_id = str(msg["from"]["id"])
                    chat_id = str(msg["chat"]["id"])
                    message_id = str(msg.get("message_id", ""))
                    text = msg.get("text") or ""
                    if allowed and int(msg["from"]["id"]) not in allowed:
                        logger.debug("user not in whitelist: %s", user_id)
                        continue
                    if not limiter.allow(user_id):
                        async with httpx.AsyncClient() as client:
                            await client.post(
                                f"{base_url}/sendMessage",
                                json={"chat_id": chat_id, "text": "Rate limit exceeded. Try again later."},
                                timeout=5.0,
                            )
                        continue
                    reasoning = "/reasoning" in text or "reasoning" in text.lower()
                    if reasoning:
                        text = text.replace("/reasoning", "").strip()
                    text = sanitize_text(text)
                    await bus.publish_incoming(
                        IncomingMessage(
                            message_id=message_id,
                            user_id=user_id,
                            chat_id=chat_id,
                            text=text,
                            reasoning_requested=reasoning,
                        )
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("poll error: %s", e)
                await asyncio.sleep(5)

    async def run_listener() -> None:
        await bus.run_listener()

    await asyncio.gather(poll(), run_listener())


def main() -> None:
    asyncio.run(run_telegram_adapter())


if __name__ == "__main__":
    main()
