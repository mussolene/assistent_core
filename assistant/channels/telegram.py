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
from assistant.core.events import ChannelKind, IncomingMessage, OutgoingReply, StreamToken
from assistant.core.logging_config import setup_logging

logger = logging.getLogger(__name__)

STREAM_EDIT_INTERVAL = 0.2
STREAM_PLACEHOLDER = "…"
MAX_MESSAGE_LENGTH = 4096
# Лимит Telegram на одно сообщение; длинные тексты режем на чанки (как в OpenClaw textChunkLimit: 4000)
TEXT_CHUNK_LIMIT = 4000
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
# Максимальный размер скачиваемого файла (байт) для сохранения в хранилище (итерация 3.1)
TELEGRAM_DOWNLOAD_MAX_BYTES = 20 * 1024 * 1024


def _get_telegram_downloads_dir() -> str:
    """Каталог для сохранения скачанных из Telegram файлов (песочница или временное хранилище)."""
    path = (
        os.getenv("TELEGRAM_DOWNLOADS_DIR", "").strip()
        or os.getenv("SANDBOX_WORKSPACE_DIR", "").strip()
        or os.getenv("WORKSPACE_DIR", "").strip()
    )
    if path:
        base = path.rstrip("/")
        return f"{base}/telegram_uploads"
    return "/tmp/telegram_downloads"


async def _download_telegram_attachment(
    token: str,
    file_id: str,
    dest_dir: str,
    filename: str,
    client: httpx.AsyncClient,
) -> Optional[str]:
    """Скачать файл по file_id через getFile, сохранить в dest_dir. Возвращает путь или None при ошибке."""
    if not token or not file_id:
        return None
    try:
        r = await client.get(
            f"{TELEGRAM_API}{token}/getFile",
            params={"file_id": file_id},
            timeout=10.0,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        if not data.get("ok"):
            return None
        file_path = (data.get("result") or {}).get("file_path")
        if not file_path:
            return None
        download_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
        r2 = await client.get(download_url, timeout=30.0)
        if r2.status_code != 200:
            return None
        content = r2.content
        if len(content) > TELEGRAM_DOWNLOAD_MAX_BYTES:
            logger.warning("telegram file too large, skip save: %s bytes", len(content))
            return None
        os.makedirs(dest_dir, exist_ok=True)
        safe_name = re.sub(r"[^\w\-\.]", "_", filename)[:200] or "file"
        full_path = os.path.join(dest_dir, safe_name)
        with open(full_path, "wb") as f:
            f.write(content)
        return full_path
    except Exception as e:
        logger.debug("download telegram file %s: %s", file_id, e)
        return None


# Команда с admin_only=True доступна только пользователям из TELEGRAM_ADMIN_IDS (ROADMAP 3.4)
BOT_COMMANDS = [
    {"command": "start", "description": "Начать / pairing"},
    {"command": "help", "description": "Справка"},
    {"command": "status", "description": "Статус: модель, очередь задач"},
    {"command": "reasoning", "description": "Включить режим рассуждений"},
    {"command": "settings", "description": "Ссылка на настройки"},
    {"command": "channels", "description": "Ссылка на дашборд (каналы)"},
    {"command": "repos", "description": "Склонированные репо и поиск"},
    {"command": "github", "description": "GitHub: репо и поиск"},
    {"command": "gitlab", "description": "GitLab: репо и поиск"},
    {"command": "dev", "description": "Обратная связь для агента (MCP)"},
    {"command": "restart", "description": "Запрос на перезапуск (только админы)", "admin_only": True},
]

# UX: единый тон сообщений (docs/UX_UI_ROADMAP.md)
PAIRING_SUCCESS_TEXT = "Привязка выполнена. Ваш ID добавлен в разрешённые."
RATE_LIMIT_MESSAGE = (
    "Превышен лимит запросов. Повторите через 1 мин."
)


def get_help_message_text() -> str:
    """Текст справки /help: список команд и краткое описание; админ-команды отдельно (ROADMAP 3.4)."""
    lines = ["<b>Справка</b>", ""]
    admin_cmds = []
    for c in BOT_COMMANDS:
        cmd = c.get("command", "")
        desc = c.get("description", "")
        if c.get("admin_only"):
            admin_cmds.append(f"/{cmd} — {desc}")
        else:
            lines.append(f"/{cmd} — {desc}")
    if admin_cmds:
        lines.append("")
        lines.append("<b>Для админов:</b>")
        lines.extend(admin_cmds)
    return "\n".join(lines)


def get_welcome_message_text() -> str:
    """Приветствие для /start без кода, когда пользователь ещё не в whitelist (UX_UI_ROADMAP)."""
    return (
        "Привет! Я персональный ассистент. Можете написать вопрос или задачу — я постараюсь помочь. "
        "Команды: /help — справка, /settings — настройки и дашборд."
    )


def get_settings_message_text(dashboard_url: str) -> str:
    """Текст для /settings и /channels (единый ответ)."""
    return (
        f"Настройки и дашборд: {dashboard_url}\n"
        "Там можно задать токен бота, разрешённые ID, модель, MCP и т.д."
    )


def format_status_message(
    model_name: str, task_count: int, dashboard_system_url: str | None = None
) -> str:
    """Форматирование ответа на /status (для тестов и отправки). Если передан dashboard_system_url — добавляем пояснение про задачи."""
    msg = f"<b>Статус</b>\nМодель: {model_name}\nЗадач в очереди: {task_count}"
    if dashboard_system_url:
        msg += (
            "\n\n<i>Задачи — запросы к ассистенту в обработке. "
            f"Подробнее: {_escape_html(dashboard_system_url)}</i>"
        )
    return msg


def get_config() -> dict:
    from assistant.config import get_config

    c = get_config()
    return {
        "token": c.telegram.bot_token or os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "business_connection_id": (
            c.telegram.business_connection_id or os.getenv("TELEGRAM_BUSINESS_CONNECTION_ID", "")
        ).strip(),
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


async def send_typing(telegram_base_url: str, chat_id: str) -> None:
    """Send Telegram sendChatAction(typing) for the given chat. Testable with mocked httpx."""
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{telegram_base_url}/sendChatAction",
                json={"chat_id": chat_id, "action": "typing"},
                timeout=5.0,
            )
    except Exception as e:
        logger.debug("sendChatAction failed: %s", e)


async def _answer_callback(telegram_base_url: str, callback_query_id: str, text: str = "") -> None:
    """Answer callback_query (убирает «часики» на кнопке, опционально показывает text)."""
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{telegram_base_url}/answerCallbackQuery",
                json={"callback_query_id": callback_query_id, "text": text[:200] if text else None},
                timeout=5.0,
            )
    except Exception as e:
        logger.debug("answerCallbackQuery failed: %s", e)


async def _handle_task_view_callback(
    base_url: str, chat_id: str, callback_query_id: str, task_id: str, user_id: str
) -> None:
    """
    Обработка callback task:view:id — получить задачу через скилл, отправить детали в чат.
    Итерация 10.3: ответ с деталями задачи (или «Задача не найдена») без вызова ассистента.
    """
    from assistant.skills.tasks import TaskSkill

    await _answer_callback(base_url, callback_query_id, "Ок")
    skill = TaskSkill()
    result = await skill.run({"action": "get_task", "task_id": task_id, "user_id": user_id})
    if result.get("ok") and result.get("formatted_details"):
        body = result["formatted_details"]
        dashboard_url = os.getenv("DASHBOARD_URL", "").strip()
        if dashboard_url:
            body = body + "\n\nОткрыть в дашборде: " + dashboard_url
        body = _to_telegram_html(body)
    else:
        body = _escape_html(result.get("error") or "Задача не найдена")
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{base_url}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": body or "—",
                    "parse_mode": PARSE_MODE,
                },
                timeout=10.0,
            )
    except Exception as e:
        logger.warning("sendMessage task details: %s", e)


async def _handle_task_done_callback(
    base_url: str,
    chat_id: str,
    callback_query_id: str,
    message_id: int,
    task_id: str,
    user_id: str,
) -> None:
    """
    Обработка callback task:done:id — отметить задачу выполненной и обновить сообщение со списком (итерация 10.5).
    """
    await _answer_callback(base_url, callback_query_id, "Ок")
    from assistant.skills.tasks import TaskSkill

    skill = TaskSkill()
    result = await skill.run(
        {"action": "update_task", "task_id": task_id, "user_id": user_id, "status": "done"}
    )
    if not result.get("ok"):
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{base_url}/answerCallbackQuery",
                    json={
                        "callback_query_id": callback_query_id,
                        "text": result.get("error", "Ошибка")[:200],
                    },
                    timeout=5.0,
                )
        except Exception:
            pass
        return
    list_result = await skill.run({"action": "list_tasks", "user_id": user_id, "only_actual": True})
    if (
        list_result.get("ok")
        and "text_telegram" in list_result
        and "inline_keyboard" in list_result
    ):
        text = _markdown_to_telegram_html(list_result["text_telegram"])
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{base_url}/editMessageText",
                    json={
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "text": text or "Нет актуальных задач.",
                        "parse_mode": PARSE_MODE,
                        "reply_markup": {"inline_keyboard": list_result["inline_keyboard"]},
                    },
                    timeout=5.0,
                )
        except Exception as e:
            logger.warning("editMessageText task list: %s", e)
    else:
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{base_url}/editMessageText",
                    json={
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "text": _escape_html("Задача отмечена выполненной."),
                        "parse_mode": PARSE_MODE,
                        "reply_markup": {"inline_keyboard": []},
                    },
                    timeout=5.0,
                )
        except Exception as e:
            logger.warning("editMessageText task done fallback: %s", e)


# ----- Итерация 9.2: команды /repos, /github, /gitlab — список с inline-кнопками и пагинация -----
REPOS_PAGE_SIZE = 6
REPOS_CALLBACK_PREFIX = "repos:"


def format_repos_reply_text(label: str, page: int, total: Optional[int] = None) -> str:
    """Текст сообщения для списка репо: «Страница N из K» если известен total (UX_UI п.5)."""
    if total is not None and total > 0:
        total_pages = max(1, (total + REPOS_PAGE_SIZE - 1) // REPOS_PAGE_SIZE)
        return f"Репозитории ({label}): {total} шт. Страница {page + 1} из {total_pages}."
    return f"Репозитории ({label}): страница {page + 1}."


def _is_telegram_acceptable_url(url: str) -> bool:
    """Telegram не принимает localhost/127.0.0.1 в URL кнопок. True только для публичных URL."""
    if not url or not isinstance(url, str):
        return False
    u = url.strip().lower()
    if u.startswith("http://localhost") or u.startswith("https://localhost"):
        return False
    if "127.0.0.1" in u or "localhost" in u:
        return False
    return u.startswith("https://") or u.startswith("http://")


def _repos_setup_hint(kind: str, dashboard_url: str) -> str:
    """Текст-подсказка: как настроить доступ к GitHub/GitLab или репо (для отправки пользователю)."""
    if kind == "github":
        return (
            f"Для команды /github настройте доступ: в дашборде откройте Репозитории и укажите "
            f"GITHUB_TOKEN (Personal Access Token). Ссылка: {dashboard_url}/repos"
        )
    if kind == "gitlab":
        return (
            f"Для команды /gitlab настройте доступ: в дашборде откройте Репозитории и укажите "
            f"GITLAB_TOKEN (Personal Access Token). Ссылка: {dashboard_url}/repos"
        )
    return (
        f"Для списка репо укажите GIT_WORKSPACE_DIR в дашборде (Репозитории) или добавьте "
        f"GITHUB_TOKEN / GITLAB_TOKEN. Ссылка: {dashboard_url}/repos"
    )


async def _get_repos_list_cloned(redis_url: str) -> list[dict]:
    """Список склонированных репо (workspace из Redis)."""
    try:
        from assistant.dashboard.config_store import get_config_from_redis
        from assistant.skills.git import list_cloned_repos_sync

        cfg = await get_config_from_redis(redis_url)
        workspace = (
            (cfg.get("GIT_WORKSPACE_DIR") or "").strip()
            or (cfg.get("WORKSPACE_DIR") or "").strip()
            or os.getenv("GIT_WORKSPACE_DIR", "").strip()
            or os.getenv("WORKSPACE_DIR", "/workspace").strip()
        )
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, list_cloned_repos_sync, workspace)
    except Exception as e:
        logger.debug("get_repos_list_cloned: %s", e)
        return []


async def _get_repos_list_github(redis_url: str, page: int = 1) -> dict:
    """Список репо пользователя на GitHub (токен из Redis)."""
    try:
        from assistant.dashboard.config_store import get_config_from_redis
        from assistant.skills.git_platform import list_github_user_repos

        cfg = await get_config_from_redis(redis_url)
        token = (cfg.get("GITHUB_TOKEN") or "").strip()
        return await list_github_user_repos(
            token=token or None, per_page=REPOS_PAGE_SIZE, page=page
        )
    except Exception as e:
        logger.debug("get_repos_list_github: %s", e)
        return {"ok": False, "error": str(e), "items": []}


async def _get_repos_list_gitlab(redis_url: str, page: int = 1) -> dict:
    """Список проектов пользователя на GitLab (токен из Redis)."""
    try:
        from assistant.dashboard.config_store import get_config_from_redis
        from assistant.skills.git_platform import list_gitlab_user_repos

        cfg = await get_config_from_redis(redis_url)
        token = (cfg.get("GITLAB_TOKEN") or "").strip()
        return await list_gitlab_user_repos(
            token=token or None, per_page=REPOS_PAGE_SIZE, page=page
        )
    except Exception as e:
        logger.debug("get_repos_list_gitlab: %s", e)
        return {"ok": False, "error": str(e), "items": []}


def _build_repos_inline_keyboard(
    kind: str,
    items: list,
    page: int,
    has_next_page: bool,
    dashboard_url: str,
) -> list[list[dict]]:
    """Собрать inline_keyboard: кнопки репо (url или callback) + Назад/Вперёд. URL дашборда не используется для localhost (Telegram их отклоняет)."""
    dashboard_repos_url = f"{dashboard_url.rstrip('/')}/repos"
    use_dashboard_url = _is_telegram_acceptable_url(dashboard_repos_url)
    rows: list[list[dict]] = []
    for it in items:
        if kind == "cloned":
            path = it.get("path") or it.get("remote_url") or "—"
            text = path[:40] + "…" if len(path) > 40 else path
            if use_dashboard_url:
                rows.append([{"text": text, "url": dashboard_repos_url}])
            else:
                rows.append(
                    [{"text": text, "callback_data": f"{REPOS_CALLBACK_PREFIX}{kind}:{page}"}]
                )
        else:
            name = (it.get("full_name") or "")[:35]
            url = it.get("html_url") or it.get("web_url") or ""
            if url:
                rows.append([{"text": name or "—", "url": url}])
    nav = []
    if page > 0:
        nav.append(
            {"text": "◀ Назад", "callback_data": f"{REPOS_CALLBACK_PREFIX}{kind}:{page - 1}"}
        )
    if has_next_page:
        nav.append(
            {"text": "Вперёд ▶", "callback_data": f"{REPOS_CALLBACK_PREFIX}{kind}:{page + 1}"}
        )
    if nav:
        rows.append(nav)
    if use_dashboard_url:
        rows.append([{"text": "Открыть дашборд", "url": dashboard_repos_url}])
    return rows


# Telegram принимает HTML — корректное отображение без «сырых» знаков разметки
PARSE_MODE = "HTML"


def _escape_html(s: str) -> str:
    """Экранировать для Telegram HTML: & < >"""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _markdown_to_telegram_html(text: str) -> str:
    """Конвертировать типичный Markdown (LLM/CommonMark) в Telegram HTML для красивого отображения в чате."""
    if not text:
        return ""
    # Сначала экранируем HTML, чтобы не сломать теги
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        # Блок кода ```...```
        if i + 3 <= n and text[i : i + 3] == "```":
            j = text.find("```", i + 3)
            if j == -1:
                out.append(_escape_html(text[i:]))
                break
            code = text[i + 3 : j].strip()
            out.append("<pre>")
            out.append(_escape_html(code))
            out.append("</pre>")
            i = j + 3
            continue
        # Инлайн `code`
        if text[i] == "`":
            j = i + 1
            while j < n and text[j] != "`":
                j += 1
            if j < n:
                out.append("<code>")
                out.append(_escape_html(text[i + 1 : j]))
                out.append("</code>")
                i = j + 1
                continue
        # **bold** или __bold__ (только двухсимвольные разделители)
        if i + 2 <= n and text[i : i + 2] in ("**", "__"):
            delim = text[i : i + 2]
            j = i + 2
            while j <= n - 2 and text[j : j + 2] != delim:
                j += 1
            if j <= n - 2:
                inner = text[i + 2 : j]
                out.append("<b>")
                out.append(_markdown_to_telegram_html(inner))
                out.append("</b>")
                i = j + 2
                continue
        # *italic* или _italic_ (одиночный * или _)
        if i + 1 <= n and text[i] in ("*", "_") and (i + 2 > n or text[i + 1] != text[i]):
            ch = text[i]
            j = i + 1
            while j < n and text[j] != ch and text[j] != "\n":
                j += 1
            if j < n and text[j] == ch:
                inner = text[i + 1 : j]
                out.append("<i>")
                out.append(_escape_html(inner))
                out.append("</i>")
                i = j + 1
                continue
        # Обычный символ
        out.append(_escape_html(text[i]))
        i += 1
    return "".join(out)


def _to_telegram_html(text: str) -> str:
    """Привести текст ответа к Telegram HTML (разметка отображается, без сырых знаков)."""
    return _markdown_to_telegram_html(text)


def _serialize_telegram_object(obj: Optional[dict]) -> Optional[dict]:
    """Для передачи в события: только dict, без кастомных типов."""
    return obj if isinstance(obj, dict) else None


def _format_checklist_update_for_agent(
    checklist_tasks_done: Optional[dict], checklist_tasks_added: Optional[dict]
) -> str:
    """Краткий текст обновления чеклиста для контекста агента."""
    parts: list[str] = []
    if checklist_tasks_done:
        done_ids = checklist_tasks_done.get("marked_as_done_task_ids") or []
        not_done_ids = checklist_tasks_done.get("marked_as_not_done_task_ids") or []
        if done_ids:
            parts.append("Отмечены как выполненные: задачи " + ", ".join(str(i) for i in done_ids))
        if not_done_ids:
            parts.append("Снята отметка: задачи " + ", ".join(str(i) for i in not_done_ids))
    if checklist_tasks_added:
        tasks = checklist_tasks_added.get("tasks") or []
        if tasks:
            texts = [t.get("text", "?") for t in tasks if isinstance(t, dict)]
            parts.append("Добавлены в чеклист: " + "; ".join(texts[:5]))
    return " ".join(parts) if parts else "[Обновление чеклиста]"


def chunk_text_for_telegram(text: str, limit: int = TEXT_CHUNK_LIMIT) -> list[str]:
    """
    Разбить длинный текст на чанки по limit символов (по границам строк где возможно).
    Как в OpenClaw: chunker + textChunkLimit для корректной отправки длинных сообщений.
    """
    if not text or len(text) <= limit:
        return [text] if text else []
    chunks: list[str] = []
    rest = text
    while rest:
        if len(rest) <= limit:
            chunks.append(rest)
            break
        block = rest[: limit + 1]
        last_nl = block.rfind("\n")
        if last_nl > limit // 2:
            cut = last_nl + 1
        else:
            cut = limit
        chunks.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    return chunks


async def probe_telegram(token: str, timeout: float = 5.0) -> dict:
    """
    Проверить бота (getMe). Для дашборда и при старте адаптера.
    Возвращает {"ok": True, "bot": {"id", "username", ...}} или {"ok": False, "error": "..."}.
    """
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{TELEGRAM_API}{token}/getMe",
                timeout=timeout,
            )
        data = r.json() if r.status_code == 200 else {}
        if data.get("ok") and data.get("result"):
            return {"ok": True, "bot": data["result"]}
        return {"ok": False, "error": data.get("description", r.text) or f"HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _confirmation_outcome_text(original_text: str, confirmed: bool) -> str:
    """Текст сообщения после выбора: убираем призыв кнопку, добавляем итог в HTML."""
    base = (original_text or "").strip()
    base = re.sub(r"\n\nВыберите ответ кнопкой ниже\.?\s*$", "", base)
    base = _to_telegram_html(base)
    if confirmed:
        return f"{base}\n\n✅ <b>Подтверждено</b>"
    return f"{base}\n\n❌ <b>Отклонено</b>"


async def _edit_message_confirmation_done(
    telegram_base_url: str, chat_id: str, message_id: int, original_text: str, confirmed: bool
) -> None:
    """Заменить текст сообщения на итог (Подтверждено/Отклонено) и убрать кнопки."""
    try:
        text = _confirmation_outcome_text(original_text, confirmed)
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{telegram_base_url}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": text,
                    "parse_mode": PARSE_MODE,
                    "reply_markup": {"inline_keyboard": []},
                },
                timeout=5.0,
            )
    except Exception as e:
        logger.debug("editMessageText confirmation done: %s", e)


async def run_telegram_adapter() -> None:
    setup_logging()
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    while True:
        cfg = get_config()
        if not cfg["token"]:
            from assistant.dashboard.config_store import get_config_from_redis

            redis_cfg = await get_config_from_redis(redis_url)
            cfg["token"] = redis_cfg.get("TELEGRAM_BOT_TOKEN") or ""
            cfg["business_connection_id"] = (
                redis_cfg.get("TELEGRAM_BUSINESS_CONNECTION_ID") or ""
            ).strip()
            ids = redis_cfg.get("TELEGRAM_ALLOWED_USER_IDS")
            cfg["allowed_ids"] = (
                set(ids)
                if isinstance(ids, list)
                else (set(int(x) for x in str(ids).split(",") if x.strip()) if ids else set())
            )
        token = cfg["token"]
        if not token:
            logger.warning(
                "TELEGRAM_BOT_TOKEN not set. Configure via Web Dashboard: http://localhost:8080 (retry in 60s)"
            )
            await asyncio.sleep(60)
            continue
        break
    allowed: Set[int] = set(cfg["allowed_ids"]) if cfg.get("allowed_ids") else set()
    rate_limit = cfg["rate_limit_per_minute"]
    poll_timeout = cfg["poll_timeout"]
    business_connection_id: str = (cfg.get("business_connection_id") or "").strip()
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
    pending_chats: set[str] = set()
    pending_lock = asyncio.Lock()
    pending_typing_task: asyncio.Task | None = None

    async def _pending_typing_loop() -> None:
        """Send typing every TYPING_ACTION_INTERVAL for chats waiting for first response."""
        while True:
            await asyncio.sleep(TYPING_ACTION_INTERVAL)
            async with pending_lock:
                chats = set(pending_chats)
            for cid in chats:
                asyncio.create_task(send_typing(base_url, cid))

    def _ensure_pending_typing_loop() -> None:
        nonlocal pending_typing_task
        if pending_typing_task is None or pending_typing_task.done():
            pending_typing_task = asyncio.create_task(_pending_typing_loop())

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
            text = _to_telegram_html(text)
            try:
                async with httpx.AsyncClient() as client:
                    if s.get("message_id") is None:
                        r = await client.post(
                            f"{base_url}/sendMessage",
                            json={
                                "chat_id": chat_id,
                                "text": text or STREAM_PLACEHOLDER,
                                "parse_mode": PARSE_MODE,
                            },
                            timeout=15.0,
                        )
                        if r.status_code == 200:
                            j = r.json()
                            s["message_id"] = j.get("result", {}).get("message_id")
                        else:
                            try:
                                logger.warning(
                                    "sendMessage stream: %s", r.json().get("description", r.text)
                                )
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
                                "parse_mode": PARSE_MODE,
                            },
                            timeout=10.0,
                        )
                        if r.status_code != 200:
                            try:
                                logger.debug(
                                    "editMessageText: %s", r.json().get("description", r.text)
                                )
                            except Exception:
                                pass
            except Exception as e:
                logger.warning("stream flush failed: %s", e)
            s["last_edit"] = time.monotonic()
            if force:
                stream_state.pop(task_id, None)

    async def _typing_loop() -> None:
        while True:
            await asyncio.sleep(TYPING_ACTION_INTERVAL)
            async with stream_lock:
                for s in stream_state.values():
                    if s.get("message_id") is None:
                        asyncio.create_task(send_typing(base_url, s["chat_id"]))

    typing_task: asyncio.Task | None = None

    async def on_stream(payload: StreamToken) -> None:
        if payload.channel != ChannelKind.TELEGRAM:
            return
        async with pending_lock:
            pending_chats.discard(payload.chat_id)
        async with stream_lock:
            if payload.task_id not in stream_state:
                stream_state[payload.task_id] = {
                    "chat_id": payload.chat_id,
                    "message_id": None,
                    "text": "",
                    "last_edit": 0.0,
                }
                asyncio.create_task(send_typing(base_url, payload.chat_id))
                nonlocal typing_task
                if typing_task is None or typing_task.done():
                    typing_task = asyncio.create_task(_typing_loop())
            s = stream_state[payload.task_id]
            s["text"] = (s["text"] or "") + (payload.token or "")
            last_edit = s["last_edit"]
            no_message_yet = s.get("message_id") is None
            has_text = bool(s["text"])
            token_has_newline = "\n" in (payload.token or "")
        # Как v1/chat/completions: при первых токенах сразу sendMessage, далее — editMessageText того же сообщения
        now = time.monotonic()
        if payload.done:
            await _flush_stream(payload.task_id, force=True)
        elif no_message_yet:
            # Сразу отправить сообщение (с текстом или плейсхолдером), затем дополнять через editMessageText
            await _flush_stream(payload.task_id, force=False)
        elif token_has_newline or (has_text and now - last_edit >= STREAM_EDIT_INTERVAL):
            await _flush_stream(payload.task_id, force=False)

    async def on_outgoing(payload: OutgoingReply) -> None:
        if payload.channel != ChannelKind.TELEGRAM:
            return
        async with pending_lock:
            pending_chats.discard(payload.chat_id)
        was_streaming = False
        async with stream_lock:
            if payload.task_id in stream_state:
                stream_state[payload.task_id]["text"] = (payload.text or "").strip()
                was_streaming = True
        if was_streaming:
            await _flush_stream(payload.task_id, force=True)
            return
        text = _strip_think_blocks(payload.text or "(empty)")
        reply_markup = getattr(payload, "reply_markup", None)
        reply_id = None
        if payload.message_id and payload.message_id.isdigit():
            mid = int(payload.message_id)
            if mid > 0:
                reply_id = mid
        # Длинные сообщения — несколькими чанками (как в OpenClaw chunker + textChunkLimit)
        raw_chunks = chunk_text_for_telegram(text, limit=TEXT_CHUNK_LIMIT)
        if not raw_chunks:
            raw_chunks = ["(empty)"]
        chunks = [_to_telegram_html(c) for c in raw_chunks]
        try:
            async with httpx.AsyncClient() as client:
                for i, chunk_text in enumerate(chunks):
                    body = {
                        "chat_id": payload.chat_id,
                        "text": chunk_text,
                        "parse_mode": PARSE_MODE,
                    }
                    if i == 0 and reply_id:
                        body["reply_to_message_id"] = reply_id
                    if reply_markup and i == len(chunks) - 1:
                        body["reply_markup"] = reply_markup
                    r = await client.post(
                        f"{base_url}/sendMessage",
                        json=body,
                        timeout=15.0,
                    )
                    if r.status_code != 200:
                        try:
                            err = r.json().get("description", r.text)
                        except Exception:
                            err = r.text
                        logger.warning("sendMessage %s: %s", r.status_code, err)
                        break
        except Exception as e:
            logger.exception("sendMessage failed: %s", e)
        # Отправить файл по ссылке (file_id из индексированных вложений)
        send_doc = getattr(payload, "send_document", None)
        if send_doc and isinstance(send_doc, dict) and send_doc.get("file_id"):
            try:
                async with httpx.AsyncClient() as client:
                    r = await client.post(
                        f"{base_url}/sendDocument",
                        json={
                            "chat_id": payload.chat_id,
                            "document": send_doc["file_id"],
                        },
                        timeout=15.0,
                    )
                if r.status_code != 200:
                    logger.warning("sendDocument %s: %s", r.status_code, r.text)
            except Exception as e:
                logger.exception("sendDocument failed: %s", e)
        # Чеклист: sendChecklist (только с business_connection_id) или текстовый список
        send_checklist = getattr(payload, "send_checklist", None)
        if send_checklist and isinstance(send_checklist, dict) and send_checklist.get("title"):
            tasks = send_checklist.get("tasks") or []
            if business_connection_id:
                try:
                    body = {
                        "business_connection_id": business_connection_id,
                        "chat_id": payload.chat_id,
                        "checklist": {
                            "title": send_checklist["title"][:255],
                            "tasks": [
                                {"id": t.get("id", i + 1), "text": (t.get("text") or "")[:100]}
                                for i, t in enumerate(tasks[:30])
                            ],
                        },
                    }
                    if "others_can_add_tasks" in send_checklist:
                        body["checklist"]["others_can_add_tasks"] = bool(
                            send_checklist["others_can_add_tasks"]
                        )
                    if "others_can_mark_tasks_as_done" in send_checklist:
                        body["checklist"]["others_can_mark_tasks_as_done"] = bool(
                            send_checklist["others_can_mark_tasks_as_done"]
                        )
                    async with httpx.AsyncClient() as client:
                        r = await client.post(
                            f"{base_url}/sendChecklist",
                            json=body,
                            timeout=15.0,
                        )
                    if r.status_code != 200:
                        logger.warning("sendChecklist %s: %s", r.status_code, r.text)
                except Exception as e:
                    logger.exception("sendChecklist failed: %s", e)
            else:
                lines = ["☑️ " + (send_checklist.get("title") or "Чеклист") + ":"]
                for t in tasks[:30]:
                    text = (t.get("text") or "?").strip()
                    lines.append("  ☐ " + text)
                fallback_text = "\n".join(lines)
                try:
                    async with httpx.AsyncClient() as client:
                        await client.post(
                            f"{base_url}/sendMessage",
                            json={
                                "chat_id": payload.chat_id,
                                "text": _to_telegram_html(fallback_text),
                                "parse_mode": PARSE_MODE,
                            },
                            timeout=15.0,
                        )
                except Exception as e:
                    logger.debug("sendMessage checklist fallback: %s", e)

    bus.subscribe_outgoing(on_outgoing)
    bus.subscribe_stream(on_stream)
    logger.info("Subscribed to assistant:outgoing_reply and stream for Telegram")
    try:
        from assistant.core.notify import get_dev_chat_id

        cid = get_dev_chat_id()
        if cid:
            logger.info("MCP notifications target chat_id=%s", cid)
        else:
            logger.warning(
                "MCP notifications: Chat ID not set. Set TELEGRAM_DEV_CHAT_ID in dashboard (Channels → Telegram) or add a user to allowed list."
            )
    except Exception as e:
        logger.debug("Could not resolve MCP dev chat id at startup: %s", e)

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
                    # Нажатие inline-кнопки (подтверждение MCP: mcp:confirm / mcp:reject)
                    cq = upd.get("callback_query")
                    if cq:
                        from assistant.core.notify import (
                            CONFIRM_CALLBACK,
                            REJECT_CALLBACK,
                            consume_pending_confirmation,
                        )

                        chat_id = str(cq["message"]["chat"]["id"])
                        callback_data = (cq.get("data") or "").strip()
                        uid_int = int(cq["from"]["id"])
                        if allowed and uid_int not in allowed:
                            await _answer_callback(base_url, cq["id"], "Доступ запрещён.")
                            continue
                        if callback_data == CONFIRM_CALLBACK:
                            if consume_pending_confirmation(chat_id, "confirm"):
                                await _answer_callback(base_url, cq["id"], "Принято.")
                                await _edit_message_confirmation_done(
                                    base_url,
                                    str(cq["message"]["chat"]["id"]),
                                    cq["message"]["message_id"],
                                    cq["message"].get("text") or "",
                                    confirmed=True,
                                )
                            else:
                                await _answer_callback(base_url, cq["id"], "Нет активного запроса.")
                        elif callback_data == REJECT_CALLBACK:
                            if consume_pending_confirmation(chat_id, "reject"):
                                await _answer_callback(base_url, cq["id"], "Отклонено.")
                                await _edit_message_confirmation_done(
                                    base_url,
                                    str(cq["message"]["chat"]["id"]),
                                    cq["message"]["message_id"],
                                    cq["message"].get("text") or "",
                                    confirmed=False,
                                )
                            else:
                                await _answer_callback(base_url, cq["id"], "Нет активного запроса.")
                        elif callback_data == "cmd:help":
                            dashboard_url = (
                                os.getenv("DASHBOARD_URL", "http://localhost:8080").rstrip("/")
                            )
                            help_text = get_help_message_text()
                            reply_markup = {
                                "inline_keyboard": [
                                    [{"text": "Открыть настройки", "url": dashboard_url}]
                                ]
                            }
                            try:
                                async with httpx.AsyncClient() as client:
                                    await client.post(
                                        f"{base_url}/sendMessage",
                                        json={
                                            "chat_id": chat_id,
                                            "text": help_text,
                                            "parse_mode": PARSE_MODE,
                                            "reply_markup": reply_markup,
                                        },
                                        timeout=5.0,
                                    )
                            except Exception as e:
                                logger.debug("sendMessage cmd:help: %s", e)
                            await _answer_callback(base_url, cq["id"], "Справка")
                        elif callback_data.startswith(REPOS_CALLBACK_PREFIX):
                            # repos:kind:page (page 0-based). Итерация 9.2
                            parts = callback_data.split(":", 2)
                            if len(parts) >= 3:
                                try:
                                    kind = parts[1]
                                    page = int(parts[2])
                                except ValueError:
                                    kind = "cloned"
                                    page = 0
                            else:
                                kind = "cloned"
                                page = 0
                            dashboard_url = os.getenv(
                                "DASHBOARD_URL", "http://localhost:8080"
                            ).rstrip("/")
                            label = (
                                "Склонированные"
                                if kind == "cloned"
                                else ("GitHub" if kind == "github" else "GitLab")
                            )
                            try:
                                if kind == "cloned":
                                    items_all = await _get_repos_list_cloned(redis_url)
                                    total = len(items_all)
                                    start = page * REPOS_PAGE_SIZE
                                    items = items_all[start : start + REPOS_PAGE_SIZE]
                                    has_next = start + len(items) < total
                                    reply = _escape_html(format_repos_reply_text(label, page, total))
                                else:
                                    api_page = page + 1
                                    out = (
                                        await _get_repos_list_github(redis_url, page=api_page)
                                        if kind == "github"
                                        else await _get_repos_list_gitlab(redis_url, page=api_page)
                                    )
                                    items = out.get("items") or []
                                    has_next = len(items) >= REPOS_PAGE_SIZE
                                    reply = _escape_html(format_repos_reply_text(label, page, None))
                                keyboard = _build_repos_inline_keyboard(
                                    kind, items, page, has_next, dashboard_url
                                )
                                async with httpx.AsyncClient() as client:
                                    await client.post(
                                        f"{base_url}/editMessageText",
                                        json={
                                            "chat_id": chat_id,
                                            "message_id": cq["message"]["message_id"],
                                            "text": reply,
                                            "parse_mode": PARSE_MODE,
                                            "reply_markup": {"inline_keyboard": keyboard},
                                        },
                                        timeout=10.0,
                                    )
                                await _answer_callback(base_url, cq["id"])
                            except Exception as e:
                                logger.debug("repos callback: %s", e)
                                await _answer_callback(base_url, cq["id"], "Ошибка")
                        elif callback_data.startswith("task:"):
                            # task:view:id — детали в адаптере; task:done:id — отметить выполненной и обновить список (10.5); остальные — в шину
                            parts = callback_data.split(":", 2)
                            if len(parts) >= 3:
                                action, task_id = parts[1], parts[2]
                                if action == "view":
                                    await _handle_task_view_callback(
                                        base_url, chat_id, cq["id"], task_id, str(uid_int)
                                    )
                                elif action == "done":
                                    msg_id = cq.get("message", {}).get("message_id")
                                    if msg_id is not None:
                                        await _handle_task_done_callback(
                                            base_url,
                                            chat_id,
                                            cq["id"],
                                            msg_id,
                                            task_id,
                                            str(uid_int),
                                        )
                                    else:
                                        await _answer_callback(base_url, cq["id"], "Ок")
                                else:
                                    instructions = {
                                        "delete": "Удали задачу с id {}.",
                                        "done": "Отметь задачу с id {} как выполненную (status=done).",
                                        "update": "Открой задачу с id {} для правки (учти предыдущее сообщение пользователя).",
                                        "add_document": "Добавь документ к задаче с id {} (данные из предыдущего сообщения или вложения).",
                                        "add_link": "Добавь ссылку к задаче с id {} (данные из предыдущего сообщения).",
                                    }
                                    text_instruction = (
                                        instructions.get(action)
                                        or "Выполни действие для задачи с id {}."
                                    ).format(task_id)
                                    await _answer_callback(base_url, cq["id"], "Ок")
                                    await bus.publish_incoming(
                                        IncomingMessage(
                                            message_id=str(cq["message"].get("message_id", "")),
                                            user_id=str(uid_int),
                                            chat_id=chat_id,
                                            text=text_instruction,
                                            metadata={
                                                "task_callback": callback_data,
                                                "task_id": task_id,
                                            },
                                        )
                                    )
                            else:
                                await _answer_callback(base_url, cq["id"])
                        else:
                            await _answer_callback(base_url, cq["id"])
                        continue
                    msg = upd.get("message") or upd.get("edited_message")
                    if not msg:
                        continue
                    user_id = str(msg["from"]["id"])
                    uid_int = int(msg["from"]["id"])
                    chat_id = str(msg["chat"]["id"])
                    message_id = str(msg.get("message_id", ""))
                    text = (msg.get("text") or msg.get("caption") or "").strip()
                    # Нормализация команд: Telegram может присылать /help@BotName — оставляем только /command
                    if text.startswith("/") and "@" in text:
                        text = text.split("@", 1)[0]
                    # Алиас опечатки (gitab → gitlab)
                    if text == "/gitab":
                        text = "/gitlab"
                    # Вложения: документ или фото — передаём в core для индексации в вектор и хранения ссылки
                    attachments: list[dict] = []
                    if msg.get("document"):
                        doc = msg["document"]
                        attachments.append(
                            {
                                "file_id": doc["file_id"],
                                "filename": doc.get("file_name") or "document",
                                "mime_type": doc.get("mime_type") or "application/octet-stream",
                                "source": "telegram",
                            }
                        )
                    if msg.get("photo"):
                        largest = msg["photo"][-1]
                        attachments.append(
                            {
                                "file_id": largest["file_id"],
                                "filename": "photo.jpg",
                                "mime_type": "image/jpeg",
                                "source": "telegram",
                            }
                        )
                    # Итерация 3.1: скачать вложения в песочницу/временное хранилище, добавить path в событие
                    if attachments and token:
                        downloads_root = _get_telegram_downloads_dir()
                        subdir = os.path.join(downloads_root, user_id)
                        async with httpx.AsyncClient() as http_client:
                            for i, att in enumerate(attachments):
                                fname = att.get("filename") or f"file_{i}"
                                unique = f"{message_id}_{i}_{fname}"
                                path = await _download_telegram_attachment(
                                    token,
                                    att["file_id"],
                                    subdir,
                                    unique,
                                    http_client,
                                )
                                if path:
                                    att["path"] = path
                                    if (
                                        fname.endswith(".txt")
                                        or att.get("mime_type", "").startswith("text/")
                                    ) and os.path.isfile(path):
                                        try:
                                            with open(
                                                path, "r", encoding="utf-8", errors="replace"
                                            ) as f:
                                                att["extracted_text"] = f.read(100_000)
                                        except Exception:
                                            pass
                    if attachments and not text:
                        text = (
                            "[Файл: "
                            + ", ".join(a.get("filename") or "файл" for a in attachments)
                            + "]"
                        )
                    # Pairing: /start CODE or /pair CODE (one-time code or secret key from dashboard)
                    start_arg = ""
                    if text.startswith("/start ") or text.startswith("/pair "):
                        start_arg = (
                            text.split(maxsplit=1)[1].strip()
                            if len(text.split(maxsplit=1)) > 1
                            else ""
                        )
                    if start_arg:
                        from assistant.dashboard.config_store import (
                            add_telegram_allowed_user,
                            consume_pairing_code,
                            consume_telegram_secret_sync,
                        )

                        loop = asyncio.get_event_loop()
                        if consume_pairing_code(redis_url, start_arg):
                            await add_telegram_allowed_user(redis_url, uid_int)
                            allowed.add(uid_int)
                            async with httpx.AsyncClient() as client:
                                await client.post(
                                    f"{base_url}/sendMessage",
                                    json={
                                        "chat_id": chat_id,
                                        "text": PAIRING_SUCCESS_TEXT,
                                        "parse_mode": PARSE_MODE,
                                    },
                                    timeout=5.0,
                                )
                            continue
                        # Попробовать секретный ключ привязки
                        loop = asyncio.get_event_loop()
                        secret_ok = await loop.run_in_executor(
                            None, consume_telegram_secret_sync, redis_url, start_arg
                        )
                        if secret_ok:
                            await add_telegram_allowed_user(redis_url, uid_int)
                            allowed.add(uid_int)
                            async with httpx.AsyncClient() as client:
                                await client.post(
                                    f"{base_url}/sendMessage",
                                    json={
                                        "chat_id": chat_id,
                                        "text": PAIRING_SUCCESS_TEXT,
                                        "parse_mode": PARSE_MODE,
                                    },
                                    timeout=5.0,
                                )
                            continue
                        # Код/ключ не подошёл — добавить в pending и подсказать
                        from assistant.dashboard.config_store import add_telegram_pending_sync

                        fr = msg.get("from") or {}
                        await loop.run_in_executor(
                            None,
                            lambda: add_telegram_pending_sync(
                                redis_url,
                                uid_int,
                                username=fr.get("username") or "",
                                first_name=fr.get("first_name") or "",
                                last_name=fr.get("last_name") or "",
                            ),
                        )
                        pending_text = _escape_html(
                            "Заявка зарегистрирована. Администратор одобрит доступ в дашборде, "
                            "либо используйте секретный ключ: /start ВАШ_КЛЮЧ"
                        )
                        async with httpx.AsyncClient() as client:
                            await client.post(
                                f"{base_url}/sendMessage",
                                json={
                                    "chat_id": chat_id,
                                    "text": pending_text,
                                    "parse_mode": PARSE_MODE,
                                },
                                timeout=5.0,
                            )
                        continue
                    # Pairing: /start or /pair when global pairing mode is on
                    if text in ("/start", "/pair"):
                        from assistant.dashboard.config_store import (
                            PAIRING_MODE_KEY,
                            add_telegram_allowed_user,
                            add_telegram_pending_sync,
                            get_config_from_redis,
                        )

                        redis_cfg = await get_config_from_redis(redis_url)
                        if (redis_cfg.get(PAIRING_MODE_KEY) or "").lower() in ("true", "1", "yes"):
                            await add_telegram_allowed_user(redis_url, uid_int)
                            allowed.add(uid_int)
                            async with httpx.AsyncClient() as client:
                                await client.post(
                                    f"{base_url}/sendMessage",
                                    json={
                                        "chat_id": chat_id,
                                        "text": PAIRING_SUCCESS_TEXT,
                                        "parse_mode": PARSE_MODE,
                                    },
                                    timeout=5.0,
                                )
                            continue
                        # /start без кода и без глобального pairing: добавить в pending, показать инструкцию
                        if allowed and uid_int not in allowed:
                            loop = asyncio.get_event_loop()
                            fr = msg.get("from") or {}
                            await loop.run_in_executor(
                                None,
                                lambda: add_telegram_pending_sync(
                                    redis_url,
                                    uid_int,
                                    username=fr.get("username") or "",
                                    first_name=fr.get("first_name") or "",
                                    last_name=fr.get("last_name") or "",
                                ),
                            )
                            dashboard_url = (
                                os.getenv("DASHBOARD_URL", "http://localhost:8080").rstrip("/")
                            )
                            pending_msg = _escape_html(
                                "Вы подали заявку на доступ. Администратор одобрит вас в дашборде, "
                                "либо введите секретный ключ: /start ВАШ_КЛЮЧ"
                            )
                            reply_markup = {
                                "inline_keyboard": [
                                    [{"text": "Открыть дашборд", "url": dashboard_url}],
                                ]
                            }
                            try:
                                async with httpx.AsyncClient() as client:
                                    await client.post(
                                        f"{base_url}/sendMessage",
                                        json={
                                            "chat_id": chat_id,
                                            "text": pending_msg,
                                            "parse_mode": PARSE_MODE,
                                            "reply_markup": reply_markup,
                                        },
                                        timeout=5.0,
                                    )
                            except Exception as e:
                                logger.debug("sendMessage pending: %s", e)
                            continue
                    if allowed and uid_int not in allowed:
                        logger.debug("user not in whitelist: %s", user_id)
                        continue
                    if not limiter.allow(user_id):
                        async with httpx.AsyncClient() as client:
                            await client.post(
                                f"{base_url}/sendMessage",
                                json={
                                    "chat_id": chat_id,
                                    "text": RATE_LIMIT_MESSAGE,
                                    "parse_mode": PARSE_MODE,
                                },
                                timeout=5.0,
                            )
                        continue
                    # /help — справка по командам (UX_UI_ROADMAP)
                    if text == "/help":
                        dashboard_url = (
                            os.getenv("DASHBOARD_URL", "http://localhost:8080").rstrip("/")
                        )
                        help_text = get_help_message_text()
                        reply_markup = {
                            "inline_keyboard": [
                                [{"text": "Открыть настройки", "url": dashboard_url}]
                            ]
                        }
                        try:
                            async with httpx.AsyncClient() as client:
                                await client.post(
                                    f"{base_url}/sendMessage",
                                    json={
                                        "chat_id": chat_id,
                                        "text": help_text,
                                        "parse_mode": PARSE_MODE,
                                        "reply_markup": reply_markup,
                                    },
                                    timeout=5.0,
                                )
                        except Exception as e:
                            logger.debug("sendMessage help: %s", e)
                        continue
                    # /settings, /channels — один ответ (ссылка на дашборд)
                    if text in ("/settings", "/channels"):
                        dashboard_url = os.getenv("DASHBOARD_URL", "http://localhost:8080").rstrip(
                            "/"
                        )
                        reply = get_settings_message_text(dashboard_url)
                        try:
                            async with httpx.AsyncClient() as client:
                                await client.post(
                                    f"{base_url}/sendMessage",
                                    json={
                                        "chat_id": chat_id,
                                        "text": reply,
                                        "parse_mode": PARSE_MODE,
                                    },
                                    timeout=5.0,
                                )
                        except Exception as e:
                            logger.debug("sendMessage settings/channels: %s", e)
                        continue
                    # /status — краткий статус: модель, очередь задач (ROADMAP 3.3)
                    if text == "/status":
                        try:
                            from assistant.dashboard.config_store import get_status_from_redis

                            data = await get_status_from_redis(redis_url)
                            model_name = str(data.get("model_name", "—"))
                            dashboard_url = os.getenv(
                                "DASHBOARD_URL", "http://localhost:8080"
                            ).rstrip("/")
                            status_text = format_status_message(
                                _escape_html(model_name),
                                data.get("task_count", 0),
                                f"{dashboard_url}/system",
                            )
                            async with httpx.AsyncClient() as client:
                                await client.post(
                                    f"{base_url}/sendMessage",
                                    json={
                                        "chat_id": chat_id,
                                        "text": status_text,
                                        "parse_mode": PARSE_MODE,
                                    },
                                    timeout=5.0,
                                )
                        except Exception as e:
                            logger.debug("sendMessage status: %s", e)
                        continue
                    # /restart — только для TELEGRAM_ADMIN_IDS (ROADMAP 3.3)
                    if text == "/restart":
                        from assistant.dashboard.config_store import (
                            TELEGRAM_ADMIN_IDS_KEY,
                            get_config_from_redis,
                            set_restart_requested,
                        )

                        redis_cfg = await get_config_from_redis(redis_url)
                        admin_ids = redis_cfg.get(TELEGRAM_ADMIN_IDS_KEY) or []
                        if not isinstance(admin_ids, list):
                            admin_ids = [int(x) for x in str(admin_ids).split(",") if str(x).strip()]
                        if uid_int not in admin_ids:
                            dashboard_url = os.getenv(
                                "DASHBOARD_URL", "http://localhost:8080"
                            ).rstrip("/")
                            deny_msg = (
                                "Недостаточно прав. Добавьте свой Telegram ID в список "
                                f"админов в дашборде: {dashboard_url} → Каналы → Telegram → ID администраторов."
                            )
                            try:
                                async with httpx.AsyncClient() as client:
                                    await client.post(
                                        f"{base_url}/sendMessage",
                                        json={
                                            "chat_id": chat_id,
                                            "text": _escape_html(deny_msg),
                                            "parse_mode": PARSE_MODE,
                                        },
                                        timeout=5.0,
                                    )
                            except Exception as e:
                                logger.debug("sendMessage restart denied: %s", e)
                            continue
                        try:
                            await set_restart_requested(redis_url, uid_int)
                            async with httpx.AsyncClient() as client:
                                await client.post(
                                    f"{base_url}/sendMessage",
                                    json={
                                        "chat_id": chat_id,
                                        "text": "Запрос на перезапуск отправлен. Ожидайте выполнения.",
                                        "parse_mode": PARSE_MODE,
                                    },
                                    timeout=5.0,
                                )
                        except Exception as e:
                            logger.debug("set_restart_requested/sendMessage: %s", e)
                        continue
                    # /repos, /github, /gitlab (и алиас /gitab) — список репо с inline-кнопками и пагинацией (9.2)
                    if text in ("/repos", "/github", "/gitlab"):
                        dashboard_url = (
                            os.getenv("DASHBOARD_URL", "http://localhost:8080")
                        ).rstrip("/")
                        kind = (
                            "cloned"
                            if text == "/repos"
                            else ("github" if text == "/github" else "gitlab")
                        )
                        label = (
                            "Склонированные"
                            if kind == "cloned"
                            else ("GitHub" if kind == "github" else "GitLab")
                        )
                        try:
                            if kind == "cloned":
                                items_all = await _get_repos_list_cloned(redis_url)
                                total = len(items_all)
                                items = items_all[:REPOS_PAGE_SIZE]
                                page = 0
                                has_next = total > REPOS_PAGE_SIZE
                                reply = _escape_html(
                                    format_repos_reply_text(label, 0, total)
                                )
                            else:
                                if kind == "github":
                                    out = await _get_repos_list_github(redis_url, page=1)
                                else:
                                    out = await _get_repos_list_gitlab(redis_url, page=1)
                                if not out.get("ok"):
                                    reply = _escape_html(
                                        _repos_setup_hint(kind, dashboard_url)
                                    )
                                    items = []
                                    page = 0
                                    has_next = False
                                else:
                                    items = out.get("items") or []
                                    page = 0
                                    has_next = len(items) >= REPOS_PAGE_SIZE
                                    reply = _escape_html(
                                        format_repos_reply_text(label, 0, None)
                                    )
                            keyboard = _build_repos_inline_keyboard(
                                kind, items, page, has_next, dashboard_url
                            )
                            fallback_sent = False
                            async with httpx.AsyncClient() as client:
                                r = await client.post(
                                    f"{base_url}/sendMessage",
                                    json={
                                        "chat_id": chat_id,
                                        "text": reply,
                                        "parse_mode": PARSE_MODE,
                                        "reply_markup": {"inline_keyboard": keyboard},
                                    },
                                    timeout=10.0,
                                )
                                if r.status_code != 200:
                                    logger.warning(
                                        "sendMessage repos %s: %s",
                                        r.status_code,
                                        r.text[:500] if r.text else "",
                                    )
                                    hint = _repos_setup_hint(kind, dashboard_url)
                                    payload: dict = {"chat_id": chat_id, "text": hint}
                                    if _is_telegram_acceptable_url(
                                        f"{dashboard_url.rstrip('/')}/repos"
                                    ):
                                        payload["reply_markup"] = {
                                            "inline_keyboard": [
                                                [
                                                    {
                                                        "text": "Открыть дашборд → Репозитории",
                                                        "url": f"{dashboard_url.rstrip('/')}/repos",
                                                    }
                                                ]
                                            ]
                                        }
                                    r2 = await client.post(
                                        f"{base_url}/sendMessage",
                                        json=payload,
                                        timeout=5.0,
                                    )
                                    if r2.status_code != 200:
                                        logger.warning(
                                            "sendMessage repos fallback %s: %s",
                                            r2.status_code,
                                            r2.text[:300] if r2.text else "",
                                        )
                                    fallback_sent = True
                            if fallback_sent:
                                continue
                        except Exception as e:
                            logger.warning("sendMessage repos list: %s", e)
                            try:
                                hint = _repos_setup_hint(kind, dashboard_url)
                                payload = {"chat_id": chat_id, "text": hint}
                                if _is_telegram_acceptable_url(
                                    f"{dashboard_url.rstrip('/')}/repos"
                                ):
                                    payload["reply_markup"] = {
                                        "inline_keyboard": [
                                            [
                                                {
                                                    "text": "Открыть дашборд → Репозитории",
                                                    "url": f"{dashboard_url.rstrip('/')}/repos",
                                                }
                                            ]
                                        ]
                                    }
                                async with httpx.AsyncClient() as client:
                                    await client.post(
                                        f"{base_url}/sendMessage",
                                        json=payload,
                                        timeout=5.0,
                                    )
                            except Exception as e2:
                                logger.debug("sendMessage repos fallback: %s", e2)
                        continue
                    # Ответ на запрос подтверждения от MCP/агента
                    try:
                        from assistant.core.notify import consume_pending_confirmation

                        if consume_pending_confirmation(chat_id, text):
                            async with httpx.AsyncClient() as client:
                                await client.post(
                                    f"{base_url}/sendMessage",
                                    json={
                                        "chat_id": chat_id,
                                        "text": "Принято.",
                                        "parse_mode": PARSE_MODE,
                                    },
                                    timeout=5.0,
                                )
                            continue
                    except Exception as e:
                        logger.debug("consume_pending_confirmation: %s", e)
                    # /dev <текст> — обратная связь для агента (MCP)
                    if text == "/dev":
                        try:
                            async with httpx.AsyncClient() as client:
                                await client.post(
                                    f"{base_url}/sendMessage",
                                    json={
                                        "chat_id": chat_id,
                                        "text": "Напишите: /dev ваш текст или пожелания для агента.",
                                        "parse_mode": PARSE_MODE,
                                    },
                                    timeout=5.0,
                                )
                        except Exception:
                            pass
                        continue
                    if text.startswith("/dev "):
                        try:
                            from assistant.core.notify import push_dev_feedback

                            push_dev_feedback(chat_id, text[5:].strip())
                            async with httpx.AsyncClient() as client:
                                await client.post(
                                    f"{base_url}/sendMessage",
                                    json={
                                        "chat_id": chat_id,
                                        "text": "Передано агенту.",
                                        "parse_mode": PARSE_MODE,
                                    },
                                    timeout=5.0,
                                )
                        except Exception as e:
                            logger.debug("push_dev_feedback: %s", e)
                        continue
                    reasoning = "/reasoning" in text or "reasoning" in text.lower()
                    if reasoning:
                        text = text.replace("/reasoning", "").strip()
                    text = sanitize_text(text)
                    # Чеклисты Telegram: передаём в core для агента (ответы на чеклист, отметки выполнено/добавлены)
                    checklist = msg.get("checklist")
                    checklist_tasks_done = msg.get("checklist_tasks_done")
                    checklist_tasks_added = msg.get("checklist_tasks_added")
                    if checklist_tasks_done or checklist_tasks_added:
                        if not text:
                            text = _format_checklist_update_for_agent(
                                checklist_tasks_done, checklist_tasks_added
                            )
                    async with pending_lock:
                        pending_chats.add(chat_id)
                        _ensure_pending_typing_loop()
                    asyncio.create_task(send_typing(base_url, chat_id))
                    await bus.publish_incoming(
                        IncomingMessage(
                            message_id=message_id,
                            user_id=user_id,
                            chat_id=chat_id,
                            text=text,
                            reasoning_requested=reasoning,
                            attachments=attachments,
                            checklist=_serialize_telegram_object(checklist),
                            checklist_tasks_done=_serialize_telegram_object(checklist_tasks_done),
                            checklist_tasks_added=_serialize_telegram_object(checklist_tasks_added),
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
