# Интеграции: Microsoft To-Do и Google Calendar (Фаза 2)

Краткое описание интеграций с внешними сервисами задач и календарём. Дорожная карта: [ANALYTICS_AND_ROADMAP_2026.md](ANALYTICS_AND_ROADMAP_2026.md), Фаза 2.

**Callback URL:** на странице дашборда **Интеграции** в блоке «Callback URL для OAuth» отображаются актуальные адреса для To-Do и Calendar (по текущему хосту); их можно скопировать и вставить в Azure AD и Google Cloud Console.

---

## Microsoft To-Do

### Настройка

1. Зарегистрировать приложение в [Azure Portal](https://portal.azure.com/) (App registrations): получить **Application (client) ID** и **Client secret**.
2. В настройках приложения указать **Redirect URI** (тип "Web"): скопировать URL из дашборда → Интеграции → «Callback URL для OAuth» (Microsoft To-Do) или использовать `https://ваш-дашборд/integrations/todo/callback` (локально: `http://localhost:8080/integrations/todo/callback`).
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

### Настройка

1. В [Google Cloud Console](https://console.cloud.google.com/) создать проект (или выбрать существующий), включить **Google Calendar API**.
2. В разделе «Учётные данные» создать **OAuth 2.0 Client ID** (тип приложения: «Веб-приложение»). Указать **Authorized redirect URIs**: скопировать URL из дашборда → Интеграции → «Callback URL для OAuth» (Google Calendar) или использовать `https://ваш-дашборд/integrations/calendar/callback` (локально: `http://localhost:8080/integrations/calendar/callback`).
3. В `.env` задать:
   - `GOOGLE_CALENDAR_CLIENT_ID` — Client ID
   - `GOOGLE_CALENDAR_CLIENT_SECRET` — Client secret
4. В дашборде открыть **Интеграции** → блок «Google Calendar» → нажать **Подключить Calendar (OAuth)**. Выполнить вход в Google и разрешить доступ. После редиректа токены сохраняются в Redis.

Токены хранятся в Redis (`assistant:integration:calendar:tokens`); при истечении access_token используется refresh_token автоматически.

### Использование

- **Скилл `integrations`**, действие **add_calendar_event**: создание события в календаре (primary). Параметры: `title` (обяз.), `start_iso`, `end_iso` (ISO datetime или date YYYY-MM-DD), `description`.
- Из диалога с ассистентом: «добавь в календарь встречу завтра в 15:00», «создай событие …» — ассистент вызовет скилл с `action=add_calendar_event`.
- **MCP** инструмент **add_calendar_event**: те же параметры (title, start_iso, end_iso, description).
