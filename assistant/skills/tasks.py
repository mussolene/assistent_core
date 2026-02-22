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


# Нормализация имени действия (модель может вернуть listtasks вместо list_tasks)
ACTION_ALIASES = {
    "listtasks": "list_tasks",
    "createtask": "create_task",
    "deletetask": "delete_task",
    "updatetask": "update_task",
    "gettask": "get_task",
    "searchtasks": "search_tasks",
    "adddocument": "add_document",
    "addlink": "add_link",
    "setreminder": "set_reminder",
    "getduereminders": "get_due_reminders",
    "formatfortelegram": "format_for_telegram",
}


def _normalize_action(action: str) -> str:
    a = (action or "").strip().lower().replace(" ", "")
    return ACTION_ALIASES.get(a, action.strip().lower() if action else "")


# Маппинг параметров без подчёркивания (модель может вернуть startdate вместо start_date)
PARAM_ALIASES = {
    "startdate": "start_date",
    "enddate": "end_date",
    "taskid": "task_id",
    "reminderat": "reminder_at",
    "timespent": "time_spent",
    "timespentminutes": "time_spent_minutes",
    "buttonaction": "button_action",
    "choiceaction": "choice_action",
    "taskids": "task_ids",
    "maxitems": "max_items",
    "onlyactual": "only_actual",
    "showdonebutton": "show_done_button",
}


def _normalize_task_params(params: dict[str, Any]) -> dict[str, Any]:
    """Подставляет стандартные ключи для вариантов без подчёркивания (startdate -> start_date)."""
    out = dict(params)
    key_lower = {k.lower(): k for k in out}
    for alias, key in PARAM_ALIASES.items():
        if key not in out and alias in key_lower:
            orig = key_lower[alias]
            out[key] = out.pop(orig, None)
    return out


def _date_to_ordinal(iso_date: str | None) -> int | None:
    """ISO date YYYY-MM-DD -> дни с эпохи (для вычисления сдвига)."""
    if not iso_date or len(iso_date) < 10:
        return None
    try:
        d = datetime.fromisoformat(iso_date[:10] + "T12:00:00+00:00")
        return d.toordinal()
    except ValueError:
        return None


def _ordinal_to_date(ordinal: int) -> str:
    """Дни с эпохи -> YYYY-MM-DD."""
    from datetime import date

    return date.fromordinal(ordinal).isoformat()


def _parse_time_spent(value: Any) -> int | None:
    """Парсит затраченное время: число (минуты), строка типа '2h', '30 min', '1.5 часа' -> минуты."""
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value * 60) if value >= 0 else None  # часы -> минуты
    s = str(value).strip().lower()
    if not s:
        return None
    import re

    # "2h", "2 ч", "2 hours", "30 min", "30 мин", "1.5 часа"
    m = re.match(r"^(\d+(?:[.,]\d+)?)\s*(h|ч|hour|hours|час|часа|часов)?\s*$", s.replace(" ", ""))
    if not m:
        m = re.match(r"^(\d+(?:[.,]\d+)?)\s*(m|min|мин|minute|minutes)?", s)
    if m:
        num = float(m.group(1).replace(",", "."))
        unit = (m.group(2) or "").strip()
        if unit in ("h", "ч", "hour", "hours", "час", "часа", "часов"):
            return int(num * 60)
        return int(num)
    return None


def _human_date(iso_date: str | None) -> str:
    """Преобразует ISO дату (YYYY-MM-DD) в короткий человекопонятный вид (дд.мм или «пн 3 мар»)."""
    if not iso_date or len(iso_date) < 10:
        return ""
    try:
        d = datetime.fromisoformat(iso_date[:10] + "T12:00:00+00:00")
        return d.strftime("%d.%m")  # 25.02
    except ValueError:
        return iso_date[:10]


def format_task_details(task: dict[str, Any]) -> str:
    """Форматирует одну задачу для показа деталей: заголовок, описание, даты, документы, ссылки."""
    title = (task.get("title") or "Без названия").replace("\n", " ")
    desc = (task.get("description") or "").strip()
    created = (task.get("created_at") or "")[:10]
    start = _human_date(task.get("start_date"))
    end = _human_date(task.get("end_date"))
    status = task.get("status") or "open"
    workload = (task.get("workload") or task.get("estimate") or "").strip()
    lines = [f"**{title}**", f"Статус: {status}. Создана: {created}."]
    if start or end:
        lines.append(f"Срок: {start or '—'} – {end or '—'}.")
    if workload:
        lines.append(f"Оценка: {workload}.")
    if desc:
        lines.append("")
        lines.append("Описание:")
        lines.append(desc)
    docs = task.get("documents") or []
    if docs:
        lines.append("")
        lines.append("Документы:")
        for d in docs:
            name = d.get("name") or d.get("url") or "—"
            url = d.get("url", "")
            lines.append(f"  • {name}" + (f" — {url}" if url else ""))
    links = task.get("links") or []
    if links:
        lines.append("")
        lines.append("Ссылки:")
        for ln in links:
            name = ln.get("name") or ln.get("url") or "—"
            url = ln.get("url", "")
            lines.append(f"  • {name}" + (f" — {url}" if url else ""))
    return "\n".join(lines)


def _format_task_created_reply(task: dict[str, Any]) -> str:
    """Краткое сообщение пользователю после создания задачи: название, срок, оценка."""
    title = (task.get("title") or "Без названия").replace("\n", " ")[:80]
    parts = [f"Задача создана: «{title}»."]
    start = _human_date(task.get("start_date"))
    end = _human_date(task.get("end_date"))
    if start or end:
        parts.append(f" Срок: {start or '…'}–{end or '…'}.")
    workload = (task.get("workload") or task.get("estimate") or "").strip()
    if workload:
        parts.append(f" Оценка: {workload}.")
    return "".join(parts).strip()


def format_tasks_list_readable(
    tasks: list[dict[str, Any]],
    include_workload: bool = False,
    include_time_spent: bool = False,
    include_created: bool = True,
) -> str:
    """
    Форматирует список задач: заголовок и дата создания (обязательно), опционально срок/оценка/затрачено.
    Нумерация 1-based для ссылок «первая задача», «вторая» и т.д.
    """
    if not tasks:
        return "Нет задач."
    lines = []
    for i, t in enumerate(tasks, 1):
        title = (t.get("title") or "Без названия").replace("\n", " ")[:60]
        created = _human_date((t.get("created_at") or "")[:10] if t.get("created_at") else None)
        created_str = f" — создана {created}" if created else ""
        status = t.get("status") or "open"
        parts = [f"{i}. **{title}**{created_str} [{status}]"]
        if include_workload and (t.get("workload") or t.get("estimate")):
            parts.append(f" оценка: {(t.get('workload') or t.get('estimate') or '').strip()}")
        if include_time_spent and (t.get("time_spent_minutes") or t.get("time_spent")):
            mins = t.get("time_spent_minutes")
            if mins is not None:
                parts.append(
                    f" затрачено: {mins // 60} ч {mins % 60} мин"
                    if mins >= 60
                    else f" затрачено: {mins} мин"
                )
        lines.append("".join(parts))
    return "Задачи:\n\n" + "\n".join(lines)


def format_tasks_for_telegram(
    tasks: list[dict[str, Any]],
    max_items: int = 20,
    action: str = "view",
    show_done_button: bool = False,
) -> tuple[str, list]:
    """
    Форматирует список задач для отправки в Telegram: текст сообщения и inline_keyboard.
    action: view | delete | update | add_document | add_link | done.
    show_done_button: добавить кнопку «✓ Выполнена» (callback task:done:id) для каждой задачи.
    """
    if not tasks:
        return "Нет задач.", []
    lines = []
    keyboard = []
    for i, t in enumerate(tasks[:max_items]):
        title = (t.get("title") or "Без названия").replace("\n", " ")[:50]
        created = _human_date((t.get("created_at") or "")[:10] if t.get("created_at") else None)
        created_str = f" ({created})" if created else ""
        status = t.get("status") or "open"
        lines.append(f"{i + 1}. **{title}**{created_str} [{status}]")
        tid = t.get("id", "")
        btn_label = f"{i + 1}. {title[:30]}{created_str}"
        if action != "view":
            action_label = {
                "delete": "Удалить",
                "update": "Правка",
                "add_document": "Документ",
                "add_link": "Ссылка",
                "done": "✓",
            }.get(action, action)
            btn_label = f"{action_label}: {title[:28]}"
        row = [{"text": btn_label, "callback_data": f"task:{action}:{tid}"}]
        if show_done_button and status == "open":
            row.append({"text": "✓ Выполнена", "callback_data": f"task:done:{tid}"})
        keyboard.append(row)
    text = "Задачи:\n\n" + "\n".join(lines)
    if len(tasks) > max_items:
        text += f"\n\n… и ещё {len(tasks) - max_items}."
    return text, keyboard


def _task_matches_query(task: dict[str, Any], query: str) -> bool:
    """Проверка: запрос входит в title или description (без учёта регистра)."""
    if not query or not query.strip():
        return True
    q = query.strip().lower()
    title = (task.get("title") or "").lower()
    desc = (task.get("description") or "").lower()
    return q in title or q in desc


def _is_actual_task(task: dict[str, Any]) -> bool:
    """Актуальная задача: статус open и (нет end_date или end_date >= сегодня)."""
    if (task.get("status") or "open") != "open":
        return False
    end = task.get("end_date")
    if not end or len(end) < 10:
        return True
    try:
        from datetime import date

        end_ord = _date_to_ordinal(end)
        today_ord = date.today().toordinal()
        return end_ord is None or end_ord >= today_ord
    except Exception:
        return True


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
                    out.append(
                        {
                            "task_id": task_id,
                            "user_id": task["user_id"],
                            "title": task.get("title") or "Задача",
                            "reminder_at": task.get("reminder_at"),
                        }
                    )
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
    Действия: create_task, delete_task, update_task, list_tasks, get_task, search_tasks (query),
    add_document, add_link, set_reminder, get_due_reminders, format_for_telegram (action=view|delete|update|...).
    """

    @property
    def name(self) -> str:
        return "tasks"

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        action = _normalize_action(params.get("action") or "")
        user_id = (params.get("user_id") or params.get("user") or "").strip()
        if not user_id:
            return {"ok": False, "error": "user_id обязателен для всех действий с задачами"}

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
            if action == "search_tasks":
                return await self._search_tasks(client, user_id, params)
            return {"ok": False, "error": f"Неизвестное действие: {action}"}
        finally:
            await client.aclose()

    async def _create(self, client, user_id: str, params: dict[str, Any]) -> dict[str, Any]:
        title = (params.get("title") or "").strip()
        if not title:
            return {"ok": False, "error": "title обязателен"}
        task_id = str(uuid.uuid4())
        now = _now_iso()
        current_year = datetime.now(timezone.utc).year
        start_date = (params.get("start_date") or "").strip() or None
        end_date = (params.get("end_date") or "").strip() or None

        def _year_of(iso: str | None) -> int | None:
            if not iso or len(iso) < 10:
                return None
            try:
                return datetime.fromisoformat(iso[:10] + "T12:00:00+00:00").year
            except ValueError:
                return None

        if start_date and (_year_of(start_date) or current_year) < current_year:
            start_date = None
        if end_date and (_year_of(end_date) or current_year) < current_year:
            end_date = None
        task = {
            "id": task_id,
            "user_id": user_id,
            "title": title,
            "description": (params.get("description") or "").strip(),
            "start_date": start_date,
            "end_date": end_date,
            "documents": list(params.get("documents") or []),
            "links": list(params.get("links") or []),
            "reminder_at": None,
            "status": (params.get("status") or "open").strip() or "open",
            "workload": (params.get("workload") or params.get("estimate") or "").strip() or None,
            "time_spent_minutes": _parse_time_spent(
                params.get("time_spent") or params.get("time_spent_minutes")
            ),
            "created_at": now,
            "updated_at": now,
        }
        await _save_task(client, task)
        await _ensure_user_list(client, user_id, task_id)
        user_reply = _format_task_created_reply(task)
        return {"ok": True, "task_id": task_id, "task": task, "user_reply": user_reply}

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
        old_start = task.get("start_date")
        old_end = task.get("end_date")
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
        if "workload" in params or "estimate" in params:
            task["workload"] = (
                str(params.get("workload") or params.get("estimate") or "").strip() or None
            )
        if "time_spent" in params or "time_spent_minutes" in params:
            mins = _parse_time_spent(params.get("time_spent") or params.get("time_spent_minutes"))
            task["time_spent_minutes"] = mins
        task["updated_at"] = _now_iso()
        await _save_task(client, task)
        cascade = params.get("cascade", True)
        if cascade and ("start_date" in params or "end_date" in params):
            await self._cascade_reschedule(
                client,
                user_id,
                task_id,
                task.get("start_date"),
                task.get("end_date"),
                old_start,
                old_end,
            )
        user_reply = None
        if params.get("status") == "done":
            user_reply = (
                f"Задача «{(task.get('title') or 'Без названия')[:50]}» отмечена выполненной."
            )
        out = {"ok": True, "task": task}
        if user_reply:
            out["user_reply"] = user_reply
        return out

    async def _cascade_reschedule(
        self,
        client,
        user_id: str,
        moved_task_id: str,
        new_start: str | None,
        new_end: str | None,
        old_start: str | None,
        old_end: str | None,
    ) -> None:
        """Сдвигает другие задачи пользователя, попадающие в новый интервал, на тот же дельта (в днях)."""
        new_s = _date_to_ordinal(new_start)
        new_e = _date_to_ordinal(new_end)
        old_s = _date_to_ordinal(old_start)
        old_e = _date_to_ordinal(old_end)
        if new_s is None and new_e is None:
            return
        delta = 0
        if old_s is not None and new_s is not None:
            delta = new_s - old_s
        elif old_e is not None and new_e is not None:
            delta = new_e - old_e
        if delta == 0:
            return
        interval_start = new_s if new_s is not None else (new_e or 0)
        interval_end = new_e if new_e is not None else (new_s or 0)
        if interval_start > interval_end:
            interval_start, interval_end = interval_end, interval_start
        raw = await client.get(_user_list_key(user_id))
        ids = json.loads(raw) if raw else []
        for tid in ids:
            if tid == moved_task_id:
                continue
            t = await _load_task(client, tid)
            if not t or not _check_owner(t, user_id):
                continue
            ts = _date_to_ordinal(t.get("start_date"))
            te = _date_to_ordinal(t.get("end_date"))
            if ts is None and te is None:
                continue
            t_start = ts if ts is not None else (te or 0)
            t_end = te if te is not None else (ts or 0)
            if t_start > t_end:
                t_start, t_end = t_end, t_start
            if t_end < interval_start or t_start > interval_end:
                continue
            if ts is not None:
                t["start_date"] = _ordinal_to_date(ts + delta)
            if te is not None:
                t["end_date"] = _ordinal_to_date(te + delta)
            t["updated_at"] = _now_iso()
            await _save_task(client, t)

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
        only_actual = params.get("only_actual") in (True, "true", "1", "yes")
        if only_actual:
            tasks = [t for t in tasks if _is_actual_task(t)]
        formatted = format_tasks_list_readable(tasks)
        out = {"ok": True, "tasks": tasks, "total": len(tasks), "formatted": formatted}
        if only_actual and tasks:
            _text, inline_keyboard = format_tasks_for_telegram(
                tasks, action="view", show_done_button=True
            )
            out["inline_keyboard"] = inline_keyboard
            out["text_telegram"] = _text
        return out

    async def _get_one(self, client, user_id: str, params: dict[str, Any]) -> dict[str, Any]:
        task_id = (params.get("task_id") or params.get("id") or "").strip()
        if not task_id:
            return {"ok": False, "error": "task_id обязателен"}
        task = await _load_task(client, task_id)
        if not task or not _check_owner(task, user_id):
            return {"ok": False, "error": "Задача не найдена или доступ запрещён"}
        return {"ok": True, "task": task, "formatted_details": format_task_details(task)}

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
            reminder_at_norm = reminder_at.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(reminder_at_norm)
            # Без суффикса таймзоны считаем UTC (как created_at и сравнения в get_due_reminders)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ts = dt.timestamp()
        except ValueError:
            return {
                "ok": False,
                "error": "reminder_at должен быть в формате ISO (например 2025-02-25T10:00:00)",
            }
        task = await _load_task(client, task_id)
        if not task or not _check_owner(task, user_id):
            return {"ok": False, "error": "Задача не найдена или доступ запрещён"}
        task["reminder_at"] = dt.isoformat()
        task["updated_at"] = _now_iso()
        await _save_task(client, task)
        await client.zadd(REDIS_REMINDERS_KEY, {task_id: ts})
        return {"ok": True, "task": task, "reminder_at": task["reminder_at"]}

    async def _get_due_reminders(self, client, params: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).timestamp()
        raw = await client.zrangebyscore(REDIS_REMINDERS_KEY, 0, now)
        out = []
        for task_id in raw:
            task = await _load_task(client, task_id)
            if task and task.get("reminder_at"):
                out.append(
                    {
                        "task_id": task_id,
                        "user_id": task.get("user_id"),
                        "title": task.get("title") or "Задача",
                        "reminder_at": task.get("reminder_at"),
                    }
                )
        return {"ok": True, "due_reminders": out}

    async def _format_for_telegram(
        self, client, user_id: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        task_ids = params.get(
            "task_ids"
        )  # опционально: только эти задачи (например результат search_tasks)
        if task_ids is not None:
            tasks = []
            for tid in task_ids:
                t = await _load_task(client, str(tid))
                if t and _check_owner(t, user_id):
                    tasks.append(t)
        else:
            raw = await client.get(_user_list_key(user_id))
            ids = json.loads(raw) if raw else []
            tasks = []
            for tid in ids:
                t = await _load_task(client, tid)
                if t and _check_owner(t, user_id):
                    tasks.append(t)
        button_action = (
            params.get("button_action") or params.get("choice_action") or "view"
        ).strip().lower() or "view"
        show_done = params.get("show_done_button") in (True, "true", "1", "yes")
        text, keyboard = format_tasks_for_telegram(
            tasks,
            max_items=int(params.get("max_items") or 20),
            action=button_action,
            show_done_button=show_done,
        )
        return {"ok": True, "text": text, "inline_keyboard": keyboard, "tasks_count": len(tasks)}

    async def _search_tasks(self, client, user_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Поиск задач по запросу (подстрока в title или description). Возвращает список задач для выбора."""
        query = (params.get("query") or params.get("q") or "").strip()
        raw = await client.get(_user_list_key(user_id))
        ids = json.loads(raw) if raw else []
        tasks = []
        for tid in ids:
            t = await _load_task(client, tid)
            if t and _check_owner(t, user_id) and _task_matches_query(t, query):
                tasks.append(t)
        return {"ok": True, "tasks": tasks, "total": len(tasks)}
