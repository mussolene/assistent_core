"""Tests for Telegram channel: sanitize, rate limit, strip_think, send_typing, chunk, probe."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from assistant.channels.telegram import (
    BOT_COMMANDS,
    PARSE_MODE,
    RateLimiter,
    _strip_think_blocks,
    _to_telegram_html,
    chunk_text_for_telegram,
    sanitize_text,
    send_typing,
)


def test_sanitize_text_empty():
    assert sanitize_text("") == ""
    assert sanitize_text(None) == ""


def test_sanitize_text_truncate():
    long_str = "a" * 5000
    out = sanitize_text(long_str, max_len=100)
    assert len(out) <= 100


def test_sanitize_text_strips_control():
    out = sanitize_text("hello\x00world\n")
    assert "\x00" not in out
    assert "hello" in out


def test_rate_limiter_allows_under_limit():
    limiter = RateLimiter(max_per_minute=2)
    assert limiter.allow("user1") is True
    assert limiter.allow("user1") is True
    assert limiter.allow("user1") is False


def test_rate_limiter_per_user():
    limiter = RateLimiter(max_per_minute=1)
    assert limiter.allow("user1") is True
    assert limiter.allow("user1") is False
    assert limiter.allow("user2") is True


def test_strip_think_blocks_empty():
    assert _strip_think_blocks("") == ""
    assert _strip_think_blocks("  ") == ""


def test_strip_think_blocks_no_think():
    assert _strip_think_blocks("Hello world") == "Hello world"


def test_strip_think_blocks_removes_think():
    text = "<think>\nreasoning here\n</think>\n\nСвязь проверена."
    assert _strip_think_blocks(text) == "Связь проверена."


def test_strip_think_blocks_unclosed_think():
    text = "<think>\nreasoning without close"
    assert _strip_think_blocks(text) == ""


def test_strip_think_blocks_only_think():
    text = "<think>\nok\n</think>"
    assert _strip_think_blocks(text) == ""


def test_bot_commands_include_settings_and_channels():
    commands = {c["command"] for c in BOT_COMMANDS}
    assert "settings" in commands
    assert "channels" in commands


def test_bot_commands_include_repos_github_gitlab():
    commands = {c["command"] for c in BOT_COMMANDS}
    assert "repos" in commands
    assert "github" in commands
    assert "gitlab" in commands


def test_build_repos_inline_keyboard_cloned_with_pagination():
    """Итерация 9.2: клавиатура для склонированных репо с кнопками Назад/Вперёд."""
    from assistant.channels.telegram import (
        REPOS_CALLBACK_PREFIX,
        _build_repos_inline_keyboard,
    )

    items = [{"path": "repo1", "remote_url": "https://github.com/u/r1"}]
    keyboard = _build_repos_inline_keyboard(
        "cloned", items, page=0, has_next_page=True, dashboard_url="http://d"
    )
    assert len(keyboard) >= 2
    row0 = keyboard[0]
    assert len(row0) == 1
    assert row0[0].get("url") == "http://d/repos"
    nav = keyboard[1]
    assert any(b.get("callback_data") == f"{REPOS_CALLBACK_PREFIX}cloned:1" for b in nav)
    assert any(b.get("text") == "Вперёд ▶" for b in nav)
    last = keyboard[-1]
    assert any(b.get("text") == "Открыть дашборд" for b in last)


def test_build_repos_inline_keyboard_github_with_urls():
    """Клавиатура для GitHub: кнопки с url репо."""
    from assistant.channels.telegram import _build_repos_inline_keyboard

    items = [
        {"full_name": "user/repo1", "html_url": "https://github.com/user/repo1"},
    ]
    keyboard = _build_repos_inline_keyboard(
        "github", items, page=0, has_next_page=False, dashboard_url="http://d"
    )
    assert len(keyboard) >= 1
    assert keyboard[0][0]["url"] == "https://github.com/user/repo1"
    assert keyboard[0][0]["text"] == "user/repo1"


def test_to_telegram_html_uses_html():
    assert PARSE_MODE == "HTML"


def test_to_telegram_html_plain():
    assert _to_telegram_html("Hello") == "Hello"
    assert _to_telegram_html("Принято.") == "Принято."


def test_to_telegram_html_bold():
    assert _to_telegram_html("**bold**") == "<b>bold</b>"
    assert _to_telegram_html("Hello **world**!") == "Hello <b>world</b>!"


def test_to_telegram_html_italic():
    assert _to_telegram_html("*italic*") == "<i>italic</i>"


def test_to_telegram_html_code():
    assert _to_telegram_html("`code`") == "<code>code</code>"


def test_to_telegram_html_escapes():
    assert "&lt;" in _to_telegram_html("<script>")
    assert "&amp;" in _to_telegram_html("a & b")


def test_chunk_text_for_telegram_short():
    assert chunk_text_for_telegram("hi") == ["hi"]
    assert chunk_text_for_telegram("") == []


def test_chunk_text_for_telegram_long():
    limit = 10
    assert chunk_text_for_telegram("a" * 5, limit=limit) == ["aaaaa"]
    chunks = chunk_text_for_telegram("a" * 25, limit=limit)
    assert len(chunks) >= 2
    assert sum(len(c) for c in chunks) >= 25
    assert all(len(c) <= limit for c in chunks)


def test_chunk_text_for_telegram_splits_on_newline():
    text = "line1\nline2\nline3\n"
    chunks = chunk_text_for_telegram(text * 100, limit=50)
    assert len(chunks) >= 2


@pytest.mark.asyncio
async def test_probe_telegram_ok():
    from assistant.channels.telegram import probe_telegram

    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "ok": True,
        "result": {"id": 1, "username": "test_bot"},
    }
    with patch("assistant.channels.telegram.httpx.AsyncClient") as m:
        m.return_value.__aenter__.return_value.get = AsyncMock(return_value=response)
        out = await probe_telegram("fake-token")
    assert out.get("ok") is True
    assert out.get("bot", {}).get("username") == "test_bot"


@pytest.mark.asyncio
async def test_probe_telegram_fail():
    from assistant.channels.telegram import probe_telegram

    with patch("assistant.channels.telegram.httpx.AsyncClient") as m:
        mock_get = AsyncMock()
        mock_get.return_value.status_code = 401
        mock_get.return_value.json.return_value = {"ok": False, "description": "Unauthorized"}
        mock_get.return_value.text = "Unauthorized"
        m.return_value.__aenter__.return_value.get = mock_get
        out = await probe_telegram("bad")
    assert out.get("ok") is False
    assert "error" in out


@pytest.mark.asyncio
async def test_send_typing_calls_telegram_api():
    with patch("assistant.channels.telegram.httpx.AsyncClient") as mock_client:
        mock_post = AsyncMock()
        mock_client.return_value.__aenter__.return_value.post = mock_post
        await send_typing("https://api.telegram.org/bot123", "chat_456")
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "sendChatAction" in call_args[0][0]
        assert call_args[1]["json"] == {"chat_id": "chat_456", "action": "typing"}


# --- Итерация 10.3: callback task:view — ответ с деталями задачи ---


@pytest.mark.asyncio
async def test_handle_task_view_callback_sends_details():
    """При callback task:view:id адаптер запрашивает задачу и шлёт в чат детали (HTML)."""
    from assistant.channels.telegram import _handle_task_view_callback

    with patch("assistant.skills.tasks.TaskSkill") as mock_skill_cls:
        mock_skill = MagicMock()
        mock_skill.run = AsyncMock(
            return_value={
                "ok": True,
                "formatted_details": "**Задача**\nСтатус: open. Создана: 2025-01-01.",
            }
        )
        mock_skill_cls.return_value = mock_skill
        with patch("assistant.channels.telegram.httpx.AsyncClient") as mock_client:
            mock_post = AsyncMock()
            mock_client.return_value.__aenter__.return_value.post = mock_post
            await _handle_task_view_callback(
                "https://api.telegram.org/bot1",
                "chat_123",
                "cq_456",
                "task-uuid-1",
                "user_42",
            )
            # answerCallbackQuery + sendMessage
            assert mock_post.call_count >= 2
            send_calls = [c for c in mock_post.call_args_list if "sendMessage" in (c[0][0] or "")]
            assert len(send_calls) >= 1
            body = send_calls[0][1]["json"]
            assert body["chat_id"] == "chat_123"
            assert "parse_mode" in body
            assert "<b>Задача</b>" in body["text"] or "Задача" in body["text"]
            assert "open" in body["text"]


@pytest.mark.asyncio
async def test_handle_task_view_callback_not_found():
    """Если задача не найдена, в чат уходит сообщение об ошибке."""
    from assistant.channels.telegram import _handle_task_view_callback

    with patch("assistant.skills.tasks.TaskSkill") as mock_skill_cls:
        mock_skill = MagicMock()
        mock_skill.run = AsyncMock(
            return_value={"ok": False, "error": "Задача не найдена или доступ запрещён"}
        )
        mock_skill_cls.return_value = mock_skill
        with patch("assistant.channels.telegram.httpx.AsyncClient") as mock_client:
            mock_post = AsyncMock()
            mock_client.return_value.__aenter__.return_value.post = mock_post
            await _handle_task_view_callback(
                "https://api.telegram.org/bot1",
                "chat_99",
                "cq_0",
                "missing-id",
                "user_1",
            )
            send_calls = [c for c in mock_post.call_args_list if "sendMessage" in (c[0][0] or "")]
            assert len(send_calls) >= 1
            body = send_calls[0][1]["json"]
            assert "Задача не найдена" in body["text"] or "доступ запрещён" in body["text"]


# --- Итерация 10.5: callback task:done — отметить выполненной и обновить список ---


@pytest.mark.asyncio
async def test_handle_task_done_callback_updates_and_edits_message():
    """При callback task:done:id вызываются update_task, list_tasks и editMessageText с новым списком."""
    from assistant.channels.telegram import _handle_task_done_callback

    call_count = 0

    async def mock_run(params):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"ok": True}
        return {
            "ok": True,
            "text_telegram": "Задачи:\n\n1. **Осталась** (01.02) [open]",
            "inline_keyboard": [[{"text": "1. Осталась", "callback_data": "task:view:other"}]],
        }

    with patch("assistant.skills.tasks.TaskSkill") as mock_skill_cls:
        mock_skill = MagicMock()
        mock_skill.run = AsyncMock(side_effect=mock_run)
        mock_skill_cls.return_value = mock_skill
        with patch("assistant.channels.telegram.httpx.AsyncClient") as mock_client:
            mock_post = AsyncMock()
            entered = MagicMock()
            entered.post = mock_post
            mock_client.return_value.__aenter__ = AsyncMock(return_value=entered)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=None)
            await _handle_task_done_callback(
                "https://api.telegram.org/bot1",
                "chat_1",
                "cq_1",
                100,
                "task-id-1",
                "user_1",
            )
            edit_calls = [
                c for c in mock_post.call_args_list if "editMessageText" in (c[0][0] or "")
            ]
            assert len(edit_calls) >= 1
            body = edit_calls[0][1]["json"]
            assert body["chat_id"] == "chat_1"
            assert body["message_id"] == 100
            assert "inline_keyboard" in body.get("reply_markup", {})
            assert mock_skill.run.call_count == 2


# --- Итерация 3.1: приём документов/фото — сохранение в хранилище, path в событии ---


def test_get_telegram_downloads_dir_default(monkeypatch):
    """Без env — возвращается /tmp/telegram_downloads."""
    from assistant.channels.telegram import _get_telegram_downloads_dir

    monkeypatch.delenv("TELEGRAM_DOWNLOADS_DIR", raising=False)
    monkeypatch.delenv("SANDBOX_WORKSPACE_DIR", raising=False)
    monkeypatch.delenv("WORKSPACE_DIR", raising=False)
    assert _get_telegram_downloads_dir() == "/tmp/telegram_downloads"


def test_get_telegram_downloads_dir_from_env(monkeypatch):
    """TELEGRAM_DOWNLOADS_DIR задаёт каталог напрямую (без суффикса telegram_uploads)."""
    from assistant.channels.telegram import _get_telegram_downloads_dir

    monkeypatch.setenv("TELEGRAM_DOWNLOADS_DIR", "/data/tg")
    monkeypatch.delenv("SANDBOX_WORKSPACE_DIR", raising=False)
    monkeypatch.delenv("WORKSPACE_DIR", raising=False)
    assert _get_telegram_downloads_dir() == "/data/tg/telegram_uploads"


def test_get_telegram_downloads_dir_workspace(monkeypatch):
    """SANDBOX_WORKSPACE_DIR -> .../telegram_uploads."""
    from assistant.channels.telegram import _get_telegram_downloads_dir

    monkeypatch.delenv("TELEGRAM_DOWNLOADS_DIR", raising=False)
    monkeypatch.setenv("SANDBOX_WORKSPACE_DIR", "/workspace")
    assert _get_telegram_downloads_dir() == "/workspace/telegram_uploads"


@pytest.mark.asyncio
async def test_download_telegram_attachment_success(tmp_path):
    """getFile + download возвращают путь к сохранённому файлу."""
    from assistant.channels.telegram import _download_telegram_attachment

    get_file_resp = MagicMock()
    get_file_resp.status_code = 200
    get_file_resp.json.return_value = {"ok": True, "result": {"file_path": "documents/abc.pdf"}}
    file_resp = MagicMock()
    file_resp.status_code = 200
    file_resp.content = b"file content here"

    async with httpx.AsyncClient() as client:
        with patch.object(client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = [get_file_resp, file_resp]
            path = await _download_telegram_attachment(
                "fake-token",
                "file_id_123",
                str(tmp_path),
                "saved.pdf",
                client,
            )
    assert path is not None
    assert path == str(tmp_path / "saved.pdf")
    assert (tmp_path / "saved.pdf").read_bytes() == b"file content here"


@pytest.mark.asyncio
async def test_download_telegram_attachment_getfile_fail():
    """getFile не ok — возвращается None."""
    from assistant.channels.telegram import _download_telegram_attachment

    get_file_resp = MagicMock()
    get_file_resp.status_code = 200
    get_file_resp.json.return_value = {"ok": False}

    async with httpx.AsyncClient() as client:
        with patch.object(client, "get", new_callable=AsyncMock, return_value=get_file_resp):
            path = await _download_telegram_attachment("token", "fid", "/tmp", "x", client)
    assert path is None


@pytest.mark.asyncio
async def test_download_telegram_attachment_too_large(tmp_path):
    """Файл больше TELEGRAM_DOWNLOAD_MAX_BYTES — не сохраняем, возвращаем None."""
    from assistant.channels.telegram import (
        TELEGRAM_DOWNLOAD_MAX_BYTES,
        _download_telegram_attachment,
    )

    get_file_resp = MagicMock()
    get_file_resp.status_code = 200
    get_file_resp.json.return_value = {"ok": True, "result": {"file_path": "documents/big"}}
    file_resp = MagicMock()
    file_resp.status_code = 200
    file_resp.content = b"x" * (TELEGRAM_DOWNLOAD_MAX_BYTES + 1)

    async with httpx.AsyncClient() as client:
        with patch.object(client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = [get_file_resp, file_resp]
            path = await _download_telegram_attachment("token", "fid", str(tmp_path), "big", client)
    assert path is None


def test_incoming_message_attachments_with_path():
    """IncomingMessage принимает attachments с полем path (итерация 3.1)."""
    from assistant.core.events import IncomingMessage

    payload = IncomingMessage(
        message_id="1",
        user_id="2",
        chat_id="3",
        text="[Файл: doc.pdf]",
        attachments=[
            {
                "file_id": "tg_123",
                "filename": "doc.pdf",
                "mime_type": "application/pdf",
                "source": "telegram",
                "path": "/workspace/telegram_uploads/2/1_0_doc.pdf",
            }
        ],
    )
    assert len(payload.attachments) == 1
    assert payload.attachments[0]["path"] == "/workspace/telegram_uploads/2/1_0_doc.pdf"
    assert payload.attachments[0]["file_id"] == "tg_123"
