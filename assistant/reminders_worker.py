"""
Воркер напоминаний (итерация 10.4): по крону вызывает get_due_reminders_sync и шлёт
уведомления в Telegram через публикацию OutgoingReply в шину (Redis).
Запуск: reminders-worker (из pyproject) или python -m assistant.reminders_worker.
Переменные: REDIS_URL (обязательно).
"""
from __future__ import annotations

import logging
import os

import redis

from assistant.core.bus import CH_OUTGOING
from assistant.core.events import ChannelKind, OutgoingReply
from assistant.skills.tasks import get_due_reminders_sync

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    reminders = get_due_reminders_sync(redis_url)
    if not reminders:
        logger.debug("No due reminders")
        return
    client = redis.from_url(redis_url, decode_responses=False)
    try:
        for r in reminders:
            task_id = r.get("task_id", "")
            user_id = r.get("user_id", "")
            title = r.get("title") or "Задача"
            text = f"Напоминание: {title}"
            payload = OutgoingReply(
                task_id=f"reminder-{task_id}",
                chat_id=str(user_id),
                text=text,
                channel=ChannelKind.TELEGRAM,
                done=True,
            )
            raw = payload.model_dump_json()
            client.publish(CH_OUTGOING, raw.encode("utf-8"))
            logger.info("Published reminder", extra={"task_id": task_id, "user_id": user_id})
    finally:
        client.close()


if __name__ == "__main__":
    main()
