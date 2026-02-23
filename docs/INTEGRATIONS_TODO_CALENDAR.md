# Интеграции: Microsoft To-Do и Google Calendar (Фаза 2)

Краткое описание интеграций с внешними сервисами задач и календарём. Дорожная карта: [ANALYTICS_AND_ROADMAP_2026.md](ANALYTICS_AND_ROADMAP_2026.md), Фаза 2.

---

## Microsoft To-Do

### Настройка

1. Зарегистрировать приложение в [Azure Portal](https://portal.azure.com/) (App registrations): получить **Application (client) ID** и **Client secret**.
2. В настройках приложения указать **Redirect URI** (тип "Web"): `https://ваш-дашборд/integrations/todo/callback` (для локальной разработки: `http://localhost:8080/integrations/todo/callback`).
3. В API permissions добавить делегированное разрешение **Microsoft Graph** → **Tasks.ReadWrite**.
4. В `.env` задать:
   - `MS_TODO_CLIENT_ID` — Application (client) ID
   - `MS_TODO_CLIENT_SECRET` — Client secret
5. В дашборде открыть **Интеграции** → блок «To-Do и календарь» → нажать **Подключить To-Do (OAuth)**. Выполнить вход в Microsoft и разрешить доступ. После редиректа токены сохраняются в Redis.

### Использование

- **Скилл `integrations`**, действие **sync_to_todo**: создание задачи в To-Do. Параметры: `title`, опционально `list_id` (если не указан — первый список).
- **list_todo_lists**: список списков задач (id и displayName) для выбора целевого списка.
- Из диалога с ассистентом: «добавь в To-Do задачу …», «создай в Microsoft To-Do …» — ассистент вызовет `integrations` с `action=sync_to_todo`.

Токены хранятся в Redis (`assistant:integration:todo:tokens`); при истечении access_token используется refresh_token автоматически.

---

## Google Calendar

Интеграция запланирована в следующих релизах: OAuth2, создание событий через Calendar API, скилл `integrations` с действием `add_calendar_event`. Пока действие возвращает подсказку о том, что сервис в разработке.
