# Дорожная карта: версия 0.2.2

Цель: **MCP как исполнитель сценариев** — любой ИИ (Cursor и др.) может создавать и просматривать задачи пользователя через MCP; плюс стабилизация и документация.

Связь: [ANALYTICS_AND_ROADMAP_2025.md](ANALYTICS_AND_ROADMAP_2025.md) (Фаза 3), [ROADMAP_0.2.1.md](ROADMAP_0.2.1.md).

---

## Цели 0.2.2 (кратко)

| Цель | Описание |
|------|----------|
| **MCP: create_task, list_tasks** | Инструменты MCP для создания задачи (title/text/phrase) и получения списка задач пользователя (chat_id = user_id). |
| **Документация MCP** | В MCP_DEV_SERVER перечислены все инструменты (notify, ask_confirmation, get_user_feedback, create_task, list_tasks) и примеры вызова. |
| **Обработка ошибок MCP** | Логирование и понятные ответы при ошибках tools/call (Redis недоступен, пустой title и т.д.). |

---

## Этапы реализации

### Этап 0.2.2.1: MCP-инструменты create_task и list_tasks

| # | Задача | Статус |
|---|--------|--------|
| 1 | Добавить в MCP_TOOLS_SPEC инструменты create_task (title, text?, phrase?) и list_tasks | ✅ |
| 2 | В _mcp_tools_call обработать create_task и list_tasks (user_id = chat_id endpoint'а), вызвать TaskSkill | ✅ |
| 3 | Тесты: вызов create_task/list_tasks через MCP API (мок Redis/skill при необходимости) | ✅ |

**Критерий приёмки:** Cursor (или другой MCP-клиент) может вызвать create_task с text «завтра купить молоко» и list_tasks и получить результат.

### Этап 0.2.2.2: Документация и стабилизация MCP

| # | Задача | Статус |
|---|--------|--------|
| 1 | MCP_DEV_SERVER.md: таблица инструментов (notify, ask_confirmation, get_user_feedback, create_task, list_tasks) и примеры | ✅ |
| 2 | При ошибке в tools/call возвращать content с текстом ошибки, не падать с 500 | ✅ |

---

## Что не входит в 0.2.2

- Microsoft To-Do и Google Calendar (OAuth) — **0.2.3+**.
- add_calendar_event, sync_task_to_todo — после появления интеграций.
- Голосовые сообщения — позже.

---

## Чеклист перед релизом 0.2.2

- [x] create_task и list_tasks доступны в MCP и работают для endpoint'а (chat_id = user_id).
- [x] Документация MCP обновлена.
- [x] Регрессия: тесты зелёные.
