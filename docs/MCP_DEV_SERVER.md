# MCP для агента (HTTP/SSE)

Доступ агента (Cursor и др.) к уведомлениям в Telegram и запросам подтверждения реализован через **HTTP API и SSE**, а не stdio. URL и секрет создаются в дашборде.

## Настройка

1. Запустите дашборд, Redis, Telegram-адаптер.
2. В дашборде откройте **MCP (агент)**.
3. Нажмите **Создать endpoint**: укажите имя (например, Cursor) и **Telegram Chat ID** (для личного чата = User ID).
4. После создания сохраните **URL** и **секрет** — секрет показывается один раз. При необходимости создайте новый секрет кнопкой **Новый секрет**.

## URL и авторизация

- **URL** имеет вид: `https://<хост>/mcp/v1/agent/<endpoint_id>`.
- **Секрет** передаётся в заголовке: `Authorization: Bearer <секрет>`.
- В MCP config на стороне потребителя укажите этот URL и секрет в `args` (или в заголовках, в зависимости от клиента).

## API (все запросы с заголовком Authorization: Bearer &lt;секрет&gt;)

| Метод | Путь | Описание |
|-------|------|----------|
| POST | `/mcp/v1/agent/<id>/notify` | Тело: `{"message": "..."}`. Отправить сообщение в Telegram. |
| POST | `/mcp/v1/agent/<id>/question` | Тело: `{"message": "..."}`. Отправить вопрос пользователю (подсказка ответить confirm/reject или текстом). |
| POST | `/mcp/v1/agent/<id>/confirmation` | Тело: `{"message": "..."}`. Запросить подтверждение: отправить в Telegram, поставить ожидание ответа. Результат приходит по SSE или при следующем ответе пользователя. |
| GET | `/mcp/v1/agent/<id>/replies` | Забрать и очистить очередь обратной связи (сообщения из `/dev ...`). Ответ: `{"ok": true, "replies": ["...", ...]}`. |
| GET | `/mcp/v1/agent/<id>/events` | **SSE** (Server-Sent Events). Долгое соединение: события `confirmation` (результат подтверждения) и `feedback` (новое сообщение от пользователя). Данные в формате JSON в поле `data`. |

## SSE

Подключитесь к `GET /mcp/v1/agent/<id>/events` с заголовком `Authorization: Bearer <секрет>`. События:

- **event: confirmation** — пользователь ответил на запрос подтверждения: `data: {"confirmed": true/false, "rejected": ..., "reply": "..."}`.
- **event: feedback** — пользователь отправил сообщение через `/dev текст`: `data: {"text": "..."}`.

Сервер периодически шлёт `: keepalive` для сохранения соединения.

## Поведение в Telegram

- Ответ на запрос подтверждения (следующее сообщение после вопроса): `confirm`/`ok`/`yes`/`да` → подтверждение, `reject`/`no`/`cancel`/`нет`/`отмена` → отмена; иначе текст попадает в `reply`. Сообщение не уходит в диалог с ассистентом.
- **/dev текст** — добавляет текст в очередь; агент забирает через GET `/replies` или получает событие `feedback` по SSE.

## Пример для клиента (curl)

```bash
# Уведомить
curl -X POST "https://your-host/mcp/v1/agent/ENDPOINT_ID/notify" \
  -H "Authorization: Bearer YOUR_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"message": "Начинаю деплой."}'

# Запросить подтверждение (ответ придёт по SSE или в следующем /replies)
curl -X POST "https://your-host/mcp/v1/agent/ENDPOINT_ID/confirmation" \
  -H "Authorization: Bearer YOUR_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"message": "Выполнить push в main?"}'

# Забрать обратную связь
curl "https://your-host/mcp/v1/agent/ENDPOINT_ID/replies" \
  -H "Authorization: Bearer YOUR_SECRET"

# SSE (долгое соединение)
curl -N "https://your-host/mcp/v1/agent/ENDPOINT_ID/events" \
  -H "Authorization: Bearer YOUR_SECRET"
```

## Stdio (опционально)

Для локального использования без HTTP по-прежнему можно запустить stdio-сервер:

```bash
python -m assistant.mcp_server
```

Он использует один «основной» чат из `TELEGRAM_DEV_CHAT_ID` или первый из разрешённых. Для нескольких endpoint'ов и URL/секрета используйте HTTP API и страницу **MCP (агент)** в дашборде.
