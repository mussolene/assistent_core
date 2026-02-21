"""Tasks skill: create, delete, update, list, date, documents/links, reminders. Storage per user in Redis."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from assistant.skills.base import BaseSkill

logger = logging.getLogger(__name__)

REDIS_TASKS_USER_PREFIX = "assistant:tasks:user:"
REDIS_TASK_PREFIX = "assistant:task:"
REDIS_REMINDERS_KEY = "assistant:reminders:due"
TASK_TTL_DAYS = 365 * 2  # 2 years


def _get_redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _task_key(task_id: str) -> str:
    return f"{REDIS_TASK_PREFIX}{task_id}"


def _user_list_key(user_id: str) -> str:
    return f"{REDIS_TASKS_USER_PREFIX}{user_id}"


async def _get_redis():
    import redis.asyncio as aioredis
    return aioredis.from_url(_get_redis_url(), decode_responses=True)


async def _load_task(client, task_id: str) -> dict[str, Any] | None:
    raw = await client.get(_task_key(task_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def _save_task(client, task: dict[str, Any]) -> None:
    task_id = task["id"]
    await client.set(_task_key(task_id), json.dumps(task), ex=TASK_TTL_DAYS * 86400)


async def _ensure_user_list(client, user_id: str, task_id: str) -> None:
    key = _user_list_key(user_id)
    raw = await client.get(key)
    ids = json.loads(raw) if raw else []
    if task_id not in ids:
        ids.append(task_id)
        await client.set(key, json.dumps(ids), ex=TASK_TTL_DAYS * 86400)


async def _remove_from_user_list(client, user_id: str, task_id: str) -> None:
    key = _user_list_key(user_id)
    raw = await client.get(key)
    if not raw:
        return
    ids = json.loads(raw)
    if task_id in ids:
        ids.remove(task_id)
        await client.set(key, json.dumps(ids), ex=TASK_TTL_DAYS * 86400)


def _check_owner(task: dict[str, Any], user_id: str) -> bool:
    return (task or {}).get("user_id") == user_id


def format_tasks_for_telegram(tasks: list[dict[str, Any]], max_items: int = 20) -> tuple[str, list]:
    """
    Форматирует список задач для отправки в Telegram: текст сообщения и inline_keyboard.
    Возвращает (text, inline_keyboard rows). Каждая кнопка — callback_data task:view:{id}.
    """
    if not tasks:
        return "Нет задач.", []
    lines = []
    keyboard = []
    for i, t in enumerate(tasks[:max_items]):
        title = (t.get("title") or "Без названия").replace("\n", " ")[:50]
        start = t.get("start_date") or ""
        end = t.get("end_date") or ""
        status = t.get("status") or "open"
        date_str = ""
        if start or end:
            date_str = f" ({start[:10] if start else '…'} — {end[:10] if end else '…'})"
        lines.append(f"{i + 1}. **{title}**{date_str} [{status}]")
        keyboard.append([{"text": f"{i + 1}. {title[:35]}", "callback_data": f"task:view:{t.get('id', '')}"}])
    text = "Задачи:\n\n" + "\n".join(lines)
    if len(tasks) > max_items:
        text += f"\n\n… и ещё {len(tasks) - max_items}."
    return text, keyboard


def get_due_reminders_sync(redis_url: str) -> list[dict[str, Any]]:
    """
    Синхронно возвращает список задач, по которым сработало напоминание (reminder_at <= now).
    Используется воркером или периодической задачей. Каждый элемент: {task_id, user_id, title, reminder_at}.
    """
    try:
        import redis
        client = redis.from_url(redis_url, decode_responses=True)
        now = datetime.now(timezone.utc).timestamp()
        # ZRANGEBYSCORE key 0 now
        raw = client.zrangebyscore(REDIS_REMINDERS_KEY, 0, now)
        client.zremrangebyscore(REDIS_REMINDERS_KEY, 0, now)
        out = []
        for task_id in raw:
            key = _task_key(task_id)
            val = client.get(key)
            if not val:
                continue
            try:
                task = json.loads(val)
                if task.get("user_id") and task.get("reminder_at"):
                    out.append({
                        "task_id": task_id,
                        "user_id": task["user_id"],
                        "title": task.get("title") or "Задача",
                        "reminder_at": task.get("reminder_at"),
                    })
            except json.JSONDecodeError:
                pass
        client.close()
        return out
    except Exception as e:
        logger.warning("get_due_reminders_sync: %s", e)
        return []


class TaskSkill(BaseSkill):
    """
    Управление задачами пользователя. Все данные хранятся в разрезе user_id; доступ только к своим задачам.
    Действия: create_task, delete_task, update_task, list_tasks, get_task, add_document, add_link, set_reminder,
    get_due_reminders, format_for_telegram.
    """

    @property
    def name(self) -> str:
        return "tasks"

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        action = (params.get("action") or "").strip().lower()
        user_id = (params.get("user_id") or params.get("user") or "").strip()
        if not user_id:
            return {"ok": False, "error": "user_id обязателен для всех действий с задачами"}

        redis_url = _get_redis_url()
        client = await _get_redis()
        try:
            await client.ping()
        except Exception as e:
            logger.warning("tasks skill redis: %s", e)
            return {"ok": False, "error": "Redis недоступен"}

        try:
            if action == "create_task":
                return await self._create(client, user_id, params)
            if action == "delete_task":
                return await self._delete(client, user_id, params)
            if action == "update_task":
                return await self._update(client, user_id, params)
            if action == "list_tasks":
                return await self._list(client, user_id, params)
            if action == "get_task":
                return await self._get_one(client, user_id, params)
            if action == "add_document":
                return await self._add_document(client, user_id, params)
            if action == "add_link":
                return await self._add_link(client, user_id, params)
            if action == "set_reminder":
                return await self._set_reminder(client, user_id, params)
            if action == "get_due_reminders":
                return await self._get_due_reminders(client, params)
            if action == "format_for_telegram":
                return await self._format_for_telegram(client, user_id, params)
            return {"ok": False, "error": f"Неизвестное действие: {action}"}
        finally:
            await client.aclose()

    async def _create(self, client, user_id: str, params: dict[str, Any]) -> dict[str, Any]:
        title = (params.get("title") or "").strip()
        if not title:
            return {"ok": False, "error": "title обязателен"}
        task_id = str(uuid.uuid4())
        now = _now_iso()
        task = {
            "id": task_id,
            "user_id": user_id,
            "title": title,
            "description": (params.get("description") or "").strip(),
            "start_date": (params.get("start_date") or "").strip() or None,
            "end_date": (params.get("end_date") or "").strip() or None,
            "documents": list(params.get("documents") or []),
            "links": list(params.get("links") or []),
            "reminder_at": None,
            "status": (params.get("status") or "open").strip() or "open",
            "created_at": now,
            "updated_at": now,
        }
        await _save_task(client, task)
        await _ensure_user_list(client, user_id, task_id)
        return {"ok": True, "task_id": task_id, "task": task}

    async def _delete(self, client, user_id: str, params: dict[str, Any]) -> dict[str, Any]:
        task_id = (params.get("task_id") or params.get("id") or "").strip()
        if not task_id:
            return {"ok": False, "error": "task_id обязателен"}
        task = await _load_task(client, task_id)
        if not task or not _check_owner(task, user_id):
            return {"ok": False, "error": "Задача не найдена или доступ запрещён"}
        await client.delete(_task_key(task_id))
        await _remove_from_user_list(client, user_id, task_id)
        # Удалить напоминание из sorted set
        await client.zrem(REDIS_REMINDERS_KEY, task_id)
        return {"ok": True, "deleted": task_id}

    async def _update(self, client, user_id: str, params: dict[str, Any]) -> dict[str, Any]:
        task_id = (params.get("task_id") or params.get("id") or "").strip()
        if not task_id:
            return {"ok": False, "error": "task_id обязателен"}
        task = await _load_task(client, task_id)
        if not task or not _check_owner(task, user_id):
            return {"ok": False, "error": "Задача не найдена или доступ запрещён"}
        if "title" in params and params["title"] is not None:
            task["title"] = str(params["title"]).strip() or task["title"]
        if "description" in params:
            task["description"] = str(params.get("description") or "").strip()
        if "start_date" in params:
            task["start_date"] = str(params.get("start_date") or "").strip() or None
        if "end_date" in params:
            task["end_date"] = str(params.get("end_date") or "").strip() or None
        if "status" in params:
            task["status"] = str(params.get("status") or "open").strip() or "open"
        task["updated_at"] = _now_iso()
        await _save_task(client, task)
        return {"ok": True, "task": task}

    async def _list(self, client, user_id: str, params: dict[str, Any]) -> dict[str, Any]:
        raw = await client.get(_user_list_key(user_id))
        ids = json.loads(raw) if raw else []
        tasks = []
        for tid in ids:
            t = await _load_task(client, tid)
            if t and _check_owner(t, user_id):
                tasks.append(t)
        status_filter = (params.get("status") or "").strip()
        if status_filter:
            tasks = [t for t in tasks if (t.get("status") or "open") == status_filter]
        return {"ok": True, "tasks": tasks, "total": len(tasks)}

    async def _get_one(self, client, user_id: str, params: dict[str, Any]) -> dict[str, Any]:
        task_id = (params.get("task_id") or params.get("id") or "").strip()
        if not task_id:
            return {"ok": False, "error": "task_id обязателен"}
        task = await _load_task(client, task_id)
        if not task or not _check_owner(task, user_id):
            return {"ok": False, "error": "Задача не найдена или доступ запрещён"}
        return {"ok": True, "task": task}

    async def _add_document(self, client, user_id: str, params: dict[str, Any]) -> dict[str, Any]:
        task_id = (params.get("task_id") or params.get("id") or "").strip()
        doc = params.get("document") or params.get("url") or {}
        if isinstance(doc, str):
            doc = {"url": doc, "name": doc}
        if not task_id or not doc:
            return {"ok": False, "error": "task_id и document (url, name) обязательны"}
        task = await _load_task(client, task_id)
        if not task or not _check_owner(task, user_id):
            return {"ok": False, "error": "Задача не найдена или доступ запрещён"}
        task.setdefault("documents", [])
        task["documents"].append(doc)
        task["updated_at"] = _now_iso()
        await _save_task(client, task)
        return {"ok": True, "task": task}

    async def _add_link(self, client, user_id: str, params: dict[str, Any]) -> dict[str, Any]:
        task_id = (params.get("task_id") or params.get("id") or "").strip()
        link = params.get("link") or params.get("url") or {}
        if isinstance(link, str):
            link = {"url": link, "name": link}
        if not task_id or not link:
            return {"ok": False, "error": "task_id и link (url, name) обязательны"}
        task = await _load_task(client, task_id)
        if not task or not _check_owner(task, user_id):
            return {"ok": False, "error": "Задача не найдена или доступ запрещён"}
        task.setdefault("links", [])
        task["links"].append(link)
        task["updated_at"] = _now_iso()
        await _save_task(client, task)
        return {"ok": True, "task": task}

    async def _set_reminder(self, client, user_id: str, params: dict[str, Any]) -> dict[str, Any]:
        task_id = (params.get("task_id") or params.get("id") or "").strip()
        reminder_at = (params.get("reminder_at") or params.get("reminder") or "").strip()
        if not task_id or not reminder_at:
            return {"ok": False, "error": "task_id и reminder_at (ISO datetime) обязательны"}
        try:
            ts = datetime.fromisoformat(reminder_at.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return {"ok": False, "error": "reminder_at должен быть в формате ISO (например 2025-02-25T10:00:00)"}
        task = await _load_task(client, task_id)
        if not task or not _check_owner(task, user_id):
            return {"ok": False, "error": "Задача не найдена или доступ запрещён"}
        task["reminder_at"] = reminder_at
        task["updated_at"] = _now_iso()
        await _save_task(client, task)
        await client.zadd(REDIS_REMINDERS_KEY, {task_id: ts})
        return {"ok": True, "task": task, "reminder_at": reminder_at}

    async def _get_due_reminders(self, client, params: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).timestamp()
        raw = await client.zrangebyscore(REDIS_REMINDERS_KEY, 0, now)
        out = []
        for task_id in raw:
            task = await _load_task(client, task_id)
            if task and task.get("reminder_at"):
                out.append({
                    "task_id": task_id,
                    "user_id": task.get("user_id"),
                    "title": task.get("title") or "Задача",
                    "reminder_at": task.get("reminder_at"),
                })
        return {"ok": True, "due_reminders": out}

    async def _format_for_telegram(self, client, user_id: str, params: dict[str, Any]) -> dict[str, Any]:
        raw = await client.get(_user_list_key(user_id))
        ids = json.loads(raw) if raw else []
        tasks = []
        for tid in ids:
            t = await _load_task(client, tid)
            if t and _check_owner(t, user_id):
                tasks.append(t)
        text, keyboard = format_tasks_for_telegram(tasks, max_items=int(params.get("max_items") or 20))
        return {"ok": True, "text": text, "inline_keyboard": keyboard, "tasks_count": len(tasks)}
