# Скилл «Задачи» (tasks)

Управление персональными задачами: создание, удаление, обновление, датирование, документы и ссылки, напоминания. **Хранение в разрезе пользователя:** задачи доступны только владельцу (по `user_id`).

---

## Хранение

- **Redis:** ключи `assistant:tasks:user:{user_id}` — список id задач; `assistant:task:{task_id}` — JSON задачи (внутри есть `user_id` для проверки доступа). TTL 2 года.
- **Напоминания:** sorted set `assistant:reminders:due` (score = Unix timestamp, member = task_id). При срабатывании напоминания запись из set удаляется.

---

## Действия скилла (action)

| action | Параметры | Описание |
|--------|-----------|----------|
| `create_task` | title, description?, start_date?, end_date?, status? | Создать задачу. |
| `delete_task` | task_id | Удалить задачу (только свою). |
| `update_task` | task_id, title?, description?, start_date?, end_date?, status? | Обновить задачу. |
| `list_tasks` | status? | Список своих задач. |
| `get_task` | task_id | Одна задача (только своя). |
| `add_document` | task_id, document (url, name) | Добавить документ к задаче. |
| `add_link` | task_id, link (url, name) | Добавить ссылку. |
| `set_reminder` | task_id, reminder_at (ISO datetime) | Установить напоминание. |
| `get_due_reminders` | — | Список сработавших напоминаний (для воркера/агента). |
| `format_for_telegram` | max_items? | Текст и inline_keyboard для отправки списка задач в чат. |

**user_id** подставляется автоматически из контекста (ToolAgent передаёт `context.user_id` для скилла `tasks`).

---

## Модель задачи

- `id`, `user_id`, `title`, `description`
- `start_date`, `end_date` (строка, например ISO date или datetime)
- `documents`: список `[{url, name}, ...]`
- `links`: список `[{url, name}, ...]`
- `reminder_at`: ISO datetime или null
- `status`: `open` | `done` | и т.д.
- `created_at`, `updated_at`

---

## Список задач в Telegram

- **format_for_telegram** возвращает `text` (разметка с номерами, датами, статусом) и `inline_keyboard` — по одной кнопке на задачу с `callback_data`: `task:view:{task_id}`.
- Агент или MCP может вызвать `tasks` с `format_for_telegram`, затем отправить в чат сообщение с `reply_markup: { inline_keyboard }` и `parse_mode: HTML` (или Markdown). Обработка нажатия кнопки `task:view:*` — в Telegram-адаптере (опционально: ответить деталями задачи или открыть в дашборде).

---

## Напоминания и будильники

- **set_reminder** записывает в задачу `reminder_at` и добавляет task_id в sorted set с score = timestamp.
- **get_due_reminders_sync(redis_url)** — синхронная функция: возвращает список задач, у которых напоминание уже должно было сработать, и удаляет их из set. Вызывается воркером или по крону.
- Рекомендуемый сценарий: отдельный процесс/скрипт раз в минуту вызывает `get_due_reminders_sync`, для каждой записи отправляет уведомление в Telegram (chat_id = user_id или через MCP notify) с текстом «Напоминание: задача …» и при необходимости подсказкой по решению.

---

## Помощь в решении

- В системном промпте ассистента указано: помогать с решением задач (предлагать шаги, напоминать о дедлайнах).
- Агент может вызывать `list_tasks` / `get_task` и на основе описания и дат предлагать план или напоминать о сроках.

---

## Тесты

- `assistant/tests/test_tasks_skill.py`: создание, список, изоляция по user_id, удаление, обновление, документы/ссылки, запрет доступа к чужой задаче, `format_tasks_for_telegram`, `get_due_reminders_sync` (мок Redis).
