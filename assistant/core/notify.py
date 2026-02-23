"""Уведомления в основной канал (Telegram) для MCP/агента: запрос подтверждения, обратная связь."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from assistant.core.events import ChannelKind, OutgoingReply

logger = logging.getLogger(__name__)

CH_OUTGOING = "assistant:outgoing_reply"
PENDING_CONFIRM_PREFIX = "assistant:pending_confirm:"
DEV_FEEDBACK_PREFIX = "assistant:dev_feedback:"
PENDING_TTL = 3600  # 1h


def _get_redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")


def get_dev_chat_id() -> str | None:
    """Chat ID для уведомлений агента (MCP). TELEGRAM_DEV_CHAT_ID или первый из TELEGRAM_ALLOWED_USER_IDS."""
    try:
        from assistant.dashboard.config_store import get_config_from_redis_sync

        cfg = get_config_from_redis_sync(_get_redis_url())
        dev = (cfg.get("TELEGRAM_DEV_CHAT_ID") or os.getenv("TELEGRAM_DEV_CHAT_ID") or "").strip()
        if dev:
            return dev
        ids = cfg.get("TELEGRAM_ALLOWED_USER_IDS")
        if isinstance(ids, list) and ids:
            return str(ids[0])
        if isinstance(ids, str) and ids:
            return ids.split(",")[0].strip()
        return None
    except Exception as e:
        logger.warning("get_dev_chat_id: %s", e)
        return None


# callback_data для inline-кнопок подтверждения (обрабатываются в Telegram-адаптере)
CONFIRM_CALLBACK = "mcp:confirm"
REJECT_CALLBACK = "mcp:reject"


def notify_to_chat(chat_id: str, text: str, reply_markup: dict | None = None) -> bool:
    """Отправить сообщение в Telegram в указанный chat_id. Опционально — reply_markup (inline_keyboard и т.д.)."""
    if not chat_id:
        return False
    try:
        import redis

        r = redis.from_url(_get_redis_url(), decode_responses=False)
        r.ping()
        payload = OutgoingReply(
            task_id="dev-notify",
            chat_id=chat_id,
            message_id="",
            text=text,
            done=True,
            channel=ChannelKind.TELEGRAM,
            reply_markup=reply_markup,
        )
        r.publish(CH_OUTGOING, payload.model_dump_json())
        r.close()
        return True
    except Exception as e:
        logger.exception("notify_to_chat: %s", e)
        return False


def send_confirmation_request(chat_id: str, message: str) -> bool:
    """Отправить запрос подтверждения с кнопками Подтвердить/Отклонить. Ставит pending и шлёт сообщение с inline-кнопками."""
    set_pending_confirmation(chat_id, message)
    prompt = f"{message}\n\nВыберите ответ кнопкой ниже."
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "✅ Подтвердить", "callback_data": CONFIRM_CALLBACK},
                {"text": "❌ Отклонить", "callback_data": REJECT_CALLBACK},
            ],
        ]
    }
    return notify_to_chat(chat_id, prompt, reply_markup=reply_markup)


def notify_main_channel(text: str) -> bool:
    """Отправить сообщение в основной канал (Telegram). Синхронно, для вызова из MCP."""
    chat_id = get_dev_chat_id()
    if not chat_id:
        logger.warning(
            "notify_main_channel: не задан Chat ID для уведомлений. "
            "Задайте TELEGRAM_DEV_CHAT_ID в дашборде (Каналы → Telegram) или добавьте пользователя в разрешённые."
        )
        return False
    return notify_to_chat(chat_id, text)


def _norm_chat_id(chat_id: str | int) -> str:
    """Единый формат chat_id для ключей Redis (избегаем расхождения дашборд/Telegram)."""
    return str(chat_id).strip()


def set_pending_confirmation(chat_id: str, message: str) -> None:
    """Поставить ожидание ответа от пользователя (confirm/reject)."""
    try:
        import redis

        r = redis.from_url(_get_redis_url(), decode_responses=True)
        cid = _norm_chat_id(chat_id)
        key = PENDING_CONFIRM_PREFIX + cid
        val = json.dumps({"message": message, "created_at": time.time(), "result": None})
        r.setex(key, PENDING_TTL, val)
        r.close()
    except Exception as e:
        logger.exception("set_pending_confirmation: %s", e)


def get_and_clear_pending_result(chat_id: str) -> dict[str, Any] | None:
    """Получить результат подтверждения (если пользователь ответил) и снять ожидание."""
    try:
        import redis

        r = redis.from_url(_get_redis_url(), decode_responses=True)
        key = PENDING_CONFIRM_PREFIX + _norm_chat_id(chat_id)
        raw = r.get(key)
        if not raw:
            r.close()
            return None
        data = json.loads(raw)
        result = data.get("result")
        if result is not None:
            r.delete(key)
        r.close()
        return result
    except Exception as e:
        logger.exception("get_and_clear_pending_result: %s", e)
        return None


def set_pending_confirmation_result(chat_id: str, result: dict[str, Any]) -> None:
    """Записать результат ответа пользователя (вызывается из Telegram-адаптера)."""
    try:
        import redis

        r = redis.from_url(_get_redis_url(), decode_responses=True)
        key = PENDING_CONFIRM_PREFIX + _norm_chat_id(chat_id)
        raw = r.get(key)
        if not raw:
            r.close()
            return
        data = json.loads(raw)
        data["result"] = result
        r.setex(key, PENDING_TTL, json.dumps(data))
        r.close()
    except Exception as e:
        logger.exception("set_pending_confirmation_result: %s", e)


def consume_pending_confirmation(chat_id: str, user_text: str) -> bool:
    """
    Если для chat_id есть ожидание подтверждения — записать ответ и вернуть True (сообщение «съедено»).
    Иначе False.
    """
    try:
        import redis

        r = redis.from_url(_get_redis_url(), decode_responses=True)
        cid = _norm_chat_id(chat_id)
        key = PENDING_CONFIRM_PREFIX + cid
        raw = r.get(key)
        r.close()
        if not raw:
            # Нормальная ситуация: нет активного запроса подтверждения (старая кнопка или обычное сообщение)
            logger.debug(
                "consume_pending_confirmation: нет активного запроса для chat_id=%s (ключ %s отсутствует)",
                cid,
                key,
            )
            return False
        data = json.loads(raw)
        if data.get("result") is not None:
            return False
        text = (user_text or "").strip().lower()
        confirmed = text in ("confirm", "ok", "yes", "да", "подтверждаю")
        rejected = text in ("reject", "no", "cancel", "нет", "отмена")
        result = {
            "confirmed": confirmed and not rejected,
            "rejected": rejected,
            "reply": user_text.strip() if user_text else "",
        }
        set_pending_confirmation_result(chat_id, result)
        try:
            from assistant.dashboard.mcp_endpoints import get_endpoint_id_for_chat, push_mcp_event

            eid = get_endpoint_id_for_chat(chat_id)
            if eid:
                push_mcp_event(eid, "confirmation", result)
        except Exception as e:
            logger.debug("push_mcp_event confirmation: %s", e)
        return True
    except Exception as e:
        logger.exception("consume_pending_confirmation: %s", e)
        return False


def push_dev_feedback(chat_id: str, text: str) -> None:
    """Добавить сообщение пользователя в очередь обратной связи для агента."""
    try:
        import redis

        r = redis.from_url(_get_redis_url(), decode_responses=True)
        key = DEV_FEEDBACK_PREFIX + chat_id
        r.rpush(key, text)
        r.expire(key, 86400 * 7)  # 7 days
        r.close()
        try:
            from assistant.dashboard.mcp_endpoints import get_endpoint_id_for_chat, push_mcp_event

            eid = get_endpoint_id_for_chat(chat_id)
            if eid:
                push_mcp_event(eid, "feedback", {"text": text})
        except Exception:
            pass
    except Exception as e:
        logger.exception("push_dev_feedback: %s", e)


def pop_dev_feedback(chat_id: str) -> list[str]:
    """Забрать и очистить накопленную обратную связь от пользователя."""
    try:
        import redis

        r = redis.from_url(_get_redis_url(), decode_responses=True)
        key = DEV_FEEDBACK_PREFIX + chat_id
        items = r.lrange(key, 0, -1)
        if items:
            r.delete(key)
        r.close()
        return list(items) if items else []
    except Exception as e:
        logger.exception("pop_dev_feedback: %s", e)
        return []
