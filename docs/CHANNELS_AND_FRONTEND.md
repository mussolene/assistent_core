# Каналы связи и фронтенд

## 1. Архитектура каналов

Все каналы работают через **единый Event Bus** (Redis pub/sub):

- Адаптер канала **публикует** `IncomingMessage` (user_id, chat_id, channel, text, …).
- Оркестратор создаёт задачу и обрабатывает её; публикует `OutgoingReply` и `StreamToken` с полем **channel**.
- Каждый адаптер **подписан** на `OutgoingReply` и `StreamToken` и обрабатывает только события со своим `channel`.

Текущие значения `ChannelKind` в `assistant.core.events`:

- `TELEGRAM` — реализован (long polling, whitelist, streaming).
- `SLACK` — запланирован.
- `WEB` — чат в браузере (дашборд/фронт).
- `EMAIL` — запланирован.

### Как добавить новый канал

1. Добавить значение в `ChannelKind` в `assistant/core/events.py` (если ещё нет).
2. Реализовать адаптер в `assistant/channels/<name>.py` по образцу Telegram:
   - подключение к Event Bus;
   - приём входящих сообщений от платформы (webhook или long poll);
   - публикация `IncomingMessage(..., channel=ChannelKind.<NAME>)`;
   - подписка на `OutgoingReply` / `StreamToken` и **фильтр** `if payload.channel != ChannelKind.<NAME>: return`;
   - отправка ответа пользователю через API платформы.
3. Конфиг и секреты — через .env / Redis (как у Telegram); при необходимости добавить секцию в дашборд.
4. Запуск: отдельный процесс/контейнер (как `telegram-adapter`) или один мультиадаптер.

---

## 2. Обзор каналов

### Telegram ✅

- Реализован: long polling, streaming, whitelist, pairing, rate limit.
- Конфиг: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_IDS` (или pairing через дашборд).

### Slack

- **API:** Slack Bolt (Python) или прямые вызовы Web API (chat.postMessage, events, shortcuts).
- **Входящие:** Events API (webhook URL) или Socket Mode (без публичного URL).
- **Исходящие:** `chat.postMessage`, при streaming — обновление сообщения или chunked answer.
- **Конфиг:** Bot Token (OAuth), Signing Secret, при webhook — публичный URL или туннель (ngrok).
- Реализация по шаблону Telegram: приём события → `IncomingMessage(channel=SLACK)` → подписка на ответы с `channel=SLACK` → `chat.postMessage` / update.

### Web (чат в браузере)

- Канал для полноценного фронта: пользователь пишет в UI, фронт шлёт запрос (REST/WebSocket) в бэкенд.
- Бэкенд публикует `IncomingMessage(channel=ChannelKind.WEB, chat_id=session_id)`.
- Ответы приходят по подписке; бэкенд отдаёт их во фронт через WebSocket или long poll / SSE.
- Позволяет сделать единый «красивый» чат в дашборде без Telegram.

### Email

- **Входящие:** IMAP polling или webhook (e.g. SendGrid Inbound Parse, Mailgun Routes).
- Парсинг письма → текст тела, from/to → `IncomingMessage(channel=EMAIL, user_id=from, chat_id=thread_id)`.
- **Исходящие:** SMTP или API провайдера (SendGrid, Mailgun); лимиты длины — разбивать на несколько писем или summary.
- Реализация проще Telegram по UI, но нужны учёт отправки и таймауты.

### iMessage

- Публичного API у Apple нет. Варианты:
  - **Мост на Mac:** скрипт/сервис на macOS (AppleScript / JXA / Swift) для чтения/отправки через приложение «Сообщения»; обмен с ассистентом по локальному API или очереди.
  - Сторонние сервисы (например, через неофициальные API) — риски по TOS и надёжности.
- В архитектуре: отдельный адаптер, который получает текст из моста и публикует `IncomingMessage(channel=IMESSAGE)`; подписывается на ответы и отдаёт их в мост.

### WhatsApp

- **Официально:** WhatsApp Business Platform (Cloud API или On-Premises), нужна бизнес-верификация и Meta-аккаунт.
- Входящие/исходящие через webhook и REST API; поддержка кнопок, списков, медиа.
- Альтернативы (WhatsApp Web неофициальные библиотеки) — против правил использования, не рекомендуются для продакшена.
- Реализация: по аналогии с Telegram — webhook endpoint, парсинг входящих, `IncomingMessage(channel=WHATSAPP)`, подписка на ответы, вызов API отправки.

---

## 3. Рекомендации по фронтенду

Текущий дашборд — Flask + `render_template_string`, один HTML-файл с встроенным CSS, секции: Telegram, Модель, MCP, Мониторинг. Для «полноценного красивого фронта» можно пойти двумя путями.

### Вариант A: Улучшить текущий Flask-дашборд

- Вынести верстку в нормальные шаблоны (Jinja2) и статику (CSS/JS) в `static/`.
- Подключить один UI-фреймворк (например, **Bootstrap 5** или **Pico CSS**) для единого вида и компонентов.
- Добавить страницу **«Чат»** (channel=WEB): форма ввода + список сообщений; запросы в API Flask, ответы через long poll или SSE (потом можно заменить на WebSocket).
- Плюсы: один репозиторий, один процесс, проще деплой. Минусы: ограниченная интерактивность без тяжёлого JS.

### Вариант B: Отдельное SPA-приложение

- **Стек:** см. детальное сравнение и рекомендации в [docs/FRONTEND_STACK.md](FRONTEND_STACK.md) (Vue + Vuetify vs React + MUI vs Svelte, Material Design, маловесность, риски).
- **Пример:** Vue 3 или React + Vite, Material-библиотека (Vuetify/MUI), TypeScript по желанию.
- **Бэкенд:** текущий Flask дашборд остаётся API (REST + опционально WebSocket для чата и стриминга).
- **Чат в браузере:** страница чата шлёт POST с текстом, получает ответ стримом (SSE/WebSocket) и публикует в Event Bus как канал WEB.
- **Настройки:** те же экраны (Telegram, Модель, MCP, Мониторинг) как страницы SPA, запросы к `/api/...` Flask.
- Плюсы: современный UX, быстрый отклик, удобно развивать сложный UI. Минусы: два артефакта (front + back), CORS, деплой статики (nginx или из Flask).

### Общие моменты

- **API:** имеющиеся роуты дашборда (настройки в Redis, auth) можно оформить как REST (`/api/config`, `/api/chat`, etc.) и потреблять с любого фронта.
- **Чат (WEB):** единая точка входа: POST сообщение → оркестратор → ответ (stream или целиком) → отображение во фронте; `chat_id` = session_id или user_id в браузере.
- **Дизайн:** тёмная тема уже задана в LAYOUT_CSS; в SPA можно сохранить палитру (--bg, --card, --accent) и развить компоненты (карточки, формы, списки).

---

## 4. План работ (кратко)

| Задача | Описание |
|--------|----------|
| Каналы | Добавить в код только нужные `ChannelKind`; новые адаптеры — по одному (Slack → WEB → Email → при необходимости iMessage/WhatsApp мосты). |
| Фронт (минимально) | Вынести CSS/HTML в шаблоны и static; подключить Pico/Bootstrap; добавить страницу «Чат» с отправкой сообщения и отображением ответа (без стрима — ок). |
| Фронт (полноценно) | Отдельный SPA (см. [FRONTEND_STACK.md](FRONTEND_STACK.md)): Vite + Vue/Vuetify или React/MUI, страницы Чат, Настройки, Мониторинг; API на Flask; стриминг — SSE или WebSocket. |

Текущий код уже готов к нескольким каналам: в событиях есть `channel`, оркестратор прокидывает его в ответы, Telegram-адаптер обрабатывает только `ChannelKind.TELEGRAM`.
