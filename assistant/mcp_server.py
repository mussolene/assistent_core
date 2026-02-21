"""
MCP-сервер для агента (Cursor): уведомления в основной канал, подтверждения, обратная связь.

Запуск: python -m assistant.mcp_server
Подключение из Cursor: stdio, command = python -m assistant.mcp_server

Инструменты:
- notify(message) — отправить сообщение в Telegram (основной канал).
- ask_confirmation(message, timeout_sec?) — отправить запрос, ждать confirm/reject от пользователя.
- get_user_feedback() — забрать накопленные сообщения от пользователя (/dev ...).
"""

from __future__ import annotations

import json
import logging
import sys
import time

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TOOLS_SPEC = [
    {
        "name": "notify",
        "description": "Отправить сообщение в основной канал (Telegram). Используй для уведомлений пользователя о ходе работы, вопросах или необходимости действия.",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string", "description": "Текст сообщения"}},
            "required": ["message"],
        },
    },
    {
        "name": "ask_confirmation",
        "description": "Запросить подтверждение у пользователя в Telegram. Отправляет сообщение и ждёт ответ confirm/reject (или произвольный текст). Используй перед важными действиями (push, deploy, выполнение команды).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Вопрос или описание действия"},
                "timeout_sec": {"type": "integer", "description": "Таймаут ожидания в секундах", "default": 300},
            },
            "required": ["message"],
        },
    },
    {
        "name": "get_user_feedback",
        "description": "Забрать накопленную обратную связь от пользователя (сообщения, отправленные через /dev в Telegram). Возвращает список строк; после вызова очередь очищается.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def handle_tools_call(name: str, arguments: dict) -> dict:
    from assistant.core.notify import (
        get_dev_chat_id,
        get_and_clear_pending_result,
        notify_main_channel,
        pop_dev_feedback,
        set_pending_confirmation,
    )

    chat_id = get_dev_chat_id()
    if not chat_id:
        return {"content": [{"type": "text", "text": "Ошибка: не задан TELEGRAM_DEV_CHAT_ID или нет разрешённых пользователей."}]}

    if name == "notify":
        msg = (arguments.get("message") or "").strip()
        if not msg:
            return {"content": [{"type": "text", "text": "Ошибка: message пустой."}]}
        ok = notify_main_channel(msg)
        return {"content": [{"type": "text", "text": "Отправлено." if ok else "Не удалось отправить."}]}

    if name == "ask_confirmation":
        msg = (arguments.get("message") or "").strip()
        timeout_sec = int(arguments.get("timeout_sec") or 300)
        if not msg:
            return {"content": [{"type": "text", "text": "Ошибка: message пустой."}]}
        prompt = f"{msg}\n\nОтветьте в Telegram: confirm / reject или свой текст."
        set_pending_confirmation(chat_id, msg)
        notify_main_channel(prompt)
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            result = get_and_clear_pending_result(chat_id)
            if result is not None:
                c = result.get("confirmed", False)
                r = result.get("reply", "")
                return {"content": [{"type": "text", "text": json.dumps({"confirmed": c, "rejected": result.get("rejected"), "reply": r})}]}
            time.sleep(1.0)
        return {"content": [{"type": "text", "text": json.dumps({"confirmed": False, "timeout": True, "reply": ""})}]}

    if name == "get_user_feedback":
        feedback = pop_dev_feedback(chat_id)
        return {"content": [{"type": "text", "text": json.dumps(feedback)}]}

    return {"content": [{"type": "text", "text": f"Неизвестный инструмент: {name}"}]}


def run_stdio() -> None:
    """Читает JSON-RPC из stdin, пишет ответы в stdout. Логи — в stderr."""
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON: %s", e)
            continue
        method = req.get("method")
        req_id = req.get("id")
        params = req.get("params") or {}

        def reply(result=None, error=None):
            out = {"jsonrpc": "2.0", "id": req_id}
            if error is not None:
                out["error"] = error
            else:
                out["result"] = result
            print(json.dumps(out), flush=True)

        if method == "initialize":
            reply({
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "assistant-dev-mcp", "version": "0.1.0"},
            })
            continue
        if method == "notified" and params.get("method") == "initialized":
            continue
        if method == "tools/list":
            reply({"tools": TOOLS_SPEC})
            continue
        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            try:
                result = handle_tools_call(name, args)
                reply(result)
            except Exception as e:
                logger.exception("tools/call %s: %s", name, e)
                reply(error={"code": -32603, "message": str(e)})
            continue
        if req_id is not None:
            reply(error={"code": -32601, "message": f"Method not found: {method}"})


def main() -> None:
    run_stdio()


if __name__ == "__main__":
    main()
