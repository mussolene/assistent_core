"""Telegram channel: long polling, whitelist, rate limit, publish to Event Bus, subscribe for replies."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Optional, Set

import httpx
from assistant.core.bus import EventBus
from assistant.core.events import IncomingMessage, OutgoingReply, StreamToken
from assistant.core.logging_config import setup_logging

logger = logging.getLogger(__name__)

STREAM_EDIT_INTERVAL = 0.2
STREAM_PLACEHOLDER = "…"
MAX_MESSAGE_LENGTH = 4096
TYPING_ACTION_INTERVAL = 4.0


def _strip_think_blocks(text: str) -> str:
    """Remove <think>...</think> blocks (model reasoning) so only the visible reply is shown."""
    if not text or "<think>" not in text:
        return text.strip()
    text = re.sub(r"<think>\s*.*?\s*</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    if "<think>" in text:
        text = text[: text.index("<think>")].strip()
    return text.strip()

TELEGRAM_API = "https://api.telegram.org/bot"

BOT_COMMANDS = [
    {"command": "start", "description": "Начать / pairing"},
    {"command": "help", "description": "Справка"},
    {"command": "reasoning", "description": "Включить режим рассуждений"},
]


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

    # Register bot commands (menu)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{base_url}/setMyCommands",
                json={"commands": BOT_COMMANDS},
                timeout=10.0,
            )
            if not r.json().get("ok"):
                logger.debug("setMyCommands: %s", r.json())
    except Exception as e:
        logger.warning("setMyCommands failed: %s", e)

    stream_state: dict[str, dict] = {}
    stream_lock = asyncio.Lock()

    async def _flush_stream(task_id: str, force: bool = False) -> None:
        async with stream_lock:
            s = stream_state.get(task_id)
            if not s:
                return
            if not s["text"] and not force and s.get("message_id") is not None:
                return
            chat_id = s["chat_id"]
            raw = s["text"] or ""
            visible = _strip_think_blocks(raw)
            text = (visible or STREAM_PLACEHOLDER)[:MAX_MESSAGE_LENGTH]
            if len(visible) > MAX_MESSAGE_LENGTH:
                text = text[: MAX_MESSAGE_LENGTH - 3] + "..."
            try:
                async with httpx.AsyncClient() as client:
                    if s.get("message_id") is None:
                        r = await client.post(
                            f"{base_url}/sendMessage",
                            json={"chat_id": chat_id, "text": text or STREAM_PLACEHOLDER},
                            timeout=15.0,
                        )
                        if r.status_code == 200:
                            j = r.json()
                            s["message_id"] = j.get("result", {}).get("message_id")
                        else:
                            try:
                                logger.warning("sendMessage stream: %s", r.json().get("description", r.text))
                            except Exception:
                                pass
                            return
                    else:
                        r = await client.post(
                            f"{base_url}/editMessageText",
                            json={
                                "chat_id": chat_id,
                                "message_id": s["message_id"],
                                "text": text or STREAM_PLACEHOLDER,
                            },
                            timeout=10.0,
                        )
                        if r.status_code != 200:
                            try:
                                logger.debug("editMessageText: %s", r.json().get("description", r.text))
                            except Exception:
                                pass
            except Exception as e:
                logger.warning("stream flush failed: %s", e)
            s["last_edit"] = time.monotonic()
            if force:
                stream_state.pop(task_id, None)

    async def _send_typing(chat_id: str) -> None:
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{base_url}/sendChatAction",
                    json={"chat_id": chat_id, "action": "typing"},
                    timeout=5.0,
                )
        except Exception as e:
            logger.debug("sendChatAction failed: %s", e)

    async def _typing_loop() -> None:
        while True:
            await asyncio.sleep(TYPING_ACTION_INTERVAL)
            async with stream_lock:
                for s in stream_state.values():
                    if s.get("message_id") is None:
                        asyncio.create_task(_send_typing(s["chat_id"]))

    typing_task: asyncio.Task | None = None

    async def on_stream(payload: StreamToken) -> None:
        async with stream_lock:
            if payload.task_id not in stream_state:
                stream_state[payload.task_id] = {
                    "chat_id": payload.chat_id,
                    "message_id": None,
                    "text": "",
                    "last_edit": 0.0,
                }
                asyncio.create_task(_send_typing(payload.chat_id))
                nonlocal typing_task
                if typing_task is None or typing_task.done():
                    typing_task = asyncio.create_task(_typing_loop())
            s = stream_state[payload.task_id]
            s["text"] = (s["text"] or "") + (payload.token or "")
            last_edit = s["last_edit"]
            no_message_yet = s.get("message_id") is None
            has_text = bool(s["text"])
            token_has_newline = "\n" in (payload.token or "")
        now = time.monotonic()
        if payload.done:
            await _flush_stream(payload.task_id, force=True)
        elif no_message_yet:
            await _flush_stream(payload.task_id, force=False)
        elif token_has_newline or (has_text and now - last_edit >= STREAM_EDIT_INTERVAL):
            await _flush_stream(payload.task_id, force=False)

    async def on_outgoing(payload: OutgoingReply) -> None:
        was_streaming = False
        async with stream_lock:
            if payload.task_id in stream_state:
                stream_state[payload.task_id]["text"] = (payload.text or "").strip()
                was_streaming = True
        if was_streaming:
            await _flush_stream(payload.task_id, force=True)
            return
        text = _strip_think_blocks(payload.text or "(empty)")
        if len(text) > MAX_MESSAGE_LENGTH:
            text = text[: MAX_MESSAGE_LENGTH - 3] + "..."
        reply_id = None
        if payload.message_id and payload.message_id.isdigit():
            mid = int(payload.message_id)
            if mid > 0:
                reply_id = mid
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"{base_url}/sendMessage",
                    json={
                        "chat_id": payload.chat_id,
                        "text": text,
                        "reply_to_message_id": reply_id,
                    },
                    timeout=15.0,
                )
                if r.status_code != 200:
                    body = r.text
                    try:
                        j = r.json()
                        body = j.get("description", body)
                    except Exception:
                        pass
                    logger.warning("sendMessage %s: %s", r.status_code, body)
        except Exception as e:
            logger.exception("sendMessage failed: %s", e)

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
                        timeout=float(poll_timeout + 15),
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
                    uid_int = int(msg["from"]["id"])
                    chat_id = str(msg["chat"]["id"])
                    message_id = str(msg.get("message_id", ""))
                    text = (msg.get("text") or "").strip()
                    # Pairing: /start or /pair when pairing mode is on
                    if text in ("/start", "/pair"):
                        from assistant.dashboard.config_store import get_config_from_redis, add_telegram_allowed_user
                        from assistant.dashboard.config_store import PAIRING_MODE_KEY
                        redis_cfg = await get_config_from_redis(redis_url)
                        if (redis_cfg.get(PAIRING_MODE_KEY) or "").lower() in ("true", "1", "yes"):
                            await add_telegram_allowed_user(redis_url, uid_int)
                            allowed.add(uid_int)
                            async with httpx.AsyncClient() as client:
                                await client.post(
                                    f"{base_url}/sendMessage",
                                    json={"chat_id": chat_id, "text": "Pairing выполнен. Ваш ID добавлен в разрешённые."},
                                    timeout=5.0,
                                )
                            continue
                    if allowed and uid_int not in allowed:
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
            except (httpx.ConnectTimeout, httpx.ReadTimeout) as e:
                logger.warning("Telegram API timeout, retry in 5s: %s", e)
                await asyncio.sleep(5)
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
