# Аудит проекта Assistant Core

Документ для приёмки, рефакторинга и поддержки: требования, flow разработки, тесты, архитектура, приёмочное тестирование, покрытие, CI.

---

## 1. Соответствие требованиям (AGENTS.md)

| Требование | Статус | Комментарий |
|------------|--------|-------------|
| Модульная архитектура (Channel → Bus → Orchestrator → Agents → Skills → Model) | ✅ | Реализовано |
| Локальные + облачные модели, fallback | ✅ | Gateway + Redis config |
| Streaming ответа | ✅ | StreamToken, editMessageText, sendChatAction(typing) |
| Reasoning режим (флаг) | ✅ | reasoning_requested, LM Studio native скрывает reasoning |
| Telegram: multi-user, ACL, pairing | ✅ | Whitelist, pairing через /start |
| Skills: filesystem, shell, git, vector_rag, mcp_adapter | ⚠️ | mcp_adapter — заглушка |
| Песочница (Docker, no root, лимиты) | ✅ | deploy.resources в compose |
| Shell whitelist | ✅ | command_whitelist |
| Memory: short-term, task, summary, vector | ✅ | MemoryManager |
| Dashboard для настройки | ✅ | Telegram, Model, MCP, Monitor |
| Автономный режим (max_iterations) | ✅ | orchestrator config |
| Масштабирование агентов (scale) | ✅ | compose scale agent=N (при необходимости отдельный сервис) |

**Не хватает по требованиям:**
- Реализованный MCP adapter (сейчас stub).
- Явной «search» skill (отдельно от vector_rag).
- DevAgent / TestAgent (опционально по AGENTS.md).

---

## 2. Стандартный flow разработки

### 2.1 Чего не хватает

- **Pre-commit / pre-push:** нет хуков (lint, format, tests). Рекомендация: `pre-commit` с ruff/black, mypy опционально.
- **Версионирование:** версия только в `pyproject.toml`, нет единого места для релизов (тегов).
- **Changelog:** нет CHANGELOG.md для пользователей.
- **Стандарт кода:** нет явного ruff/black/mypy в CI (только проверка импортов).
- **Документация кода:** часть модулей без docstring; нет Sphinx/API docs.
- **Локальный запуск тестов с Redis:** тесты, зависящие от Redis, скипаются при отсутствии — нет docker-compose для тестового Redis в CI.

### 2.2 Что стоит поправить

- **Единый стиль:** включить ruff в CI и в pre-commit, задать line-length и правила.
- **Типизация:** пройтись по публичным API (агенты, skills, bus), добавить типы; при желании — mypy в CI.
- **Конфиг:** часть настроек только в Redis (dashboard), часть в YAML/env — описать в README приоритет (Redis > env > YAML).
- **Логирование:** структурированные логи есть; убедиться, что секреты не логируются (REDACTED в bus уже есть).
- **Streaming:** см. раздел 2.5.

### 2.3 Каких тестов не хватает

- **Покрытие модулей (цель ≥90%):**
  - `channels/telegram.py`: только sanitize_text в test_channels; нет тестов on_stream, on_outgoing, _strip_think_blocks, _send_typing (с моками).
  - `dashboard/app.py`: только API test-bot/test-model/monitor и config_store; нет тестов маршрутов сохранения (save_telegram, save_model, save_mcp, remove_mcp), редиректов, flash.
  - `models/lm_studio.py`: нет тестов (generate_lm_studio, stream_lm_studio, парсинг SSE).
  - `models/gateway.py`: только local mock; нет тестов с use_lm_studio_native, fallback на cloud.
  - `core/orchestrator.py`: частичное; нет тестов со stream_callback и публикацией StreamToken.
  - `core/task_manager.py`: нет тестов create/update/get (нужен Redis или mock).
  - `memory/*`: test_memory есть; проверить покрытие summary, vector (с моком sentence_transformers).
  - `security/audit.py`, `core/logging_config.py`: нет тестов.
- **Интеграционные:** сценарий «incoming → orchestrator → assistant → stream → outgoing» с моками bus и модели.
- **E2E (опционально):** один сценарий с реальным Redis (в CI — сервис redis).

### 2.4 Mock MCP сервера для тестирования

- **Цель:** проверять работу конфигурации MCP (URL, аргументы) и, в перспективе, вызовы MCP из skill.
- **Что сделать:**
  1. **Тестовый MCP сервер (mock):** минимальный HTTP/stdio сервер в `assistant/tests/mcp_mock/` или fixture, который:
     - принимает список инструментов (tools) и при вызове возвращает фиксированный ответ;
     - опционально проверяет переданные аргументы (из dashboard: name, url, args).
  2. **Тесты dashboard:** сохранение MCP_SERVERS с args, отображение списка, удаление.
  3. **Тесты конфигурации MCP:** загрузка списка серверов из Redis в core (когда MCP adapter будет реализован) и вызов mock-сервера.
- **Файлы:** например `assistant/tests/mcp_mock_server.py` (простой FastAPI/Flask или asyncio HTTP), `assistant/tests/test_mcp_config.py`.

### 2.5 Streaming: стандартизация на OpenAI-протокол

- **Текущее состояние:**
  - Локальная модель: OpenAI-compatible `chat.completions.create(..., stream=True)` → итератор по `delta.content` (уже стандарт).
  - LM Studio native: свой SSE (`message.delta` / `reasoning.delta`); мы используем только `message.delta`.
  - Шина: свой `StreamToken(task_id, chat_id, token, done)`; Telegram редактирует одно сообщение по токенам.
- **Рекомендации:**
  - Оставить единый контракт для каналов: «строка токенов» (как сейчас). Для других провайдеров (OpenAI, Anthropic) при необходимости маппить их stream в тот же формат.
  - В коде явно задокументировать: «Streaming совместим с OpenAI Chat Completions stream (content deltas); LM Studio native маппится в те же токены».
  - Опционально: абстракция `StreamingProtocol` (OpenAI / LM Studio / другой) с одним методом `stream_tokens()` → AsyncIterator[str], чтобы gateway не раздувался.

---

## 3. Приёмочное тестирование (чек-лист фич)

| Фича | Как проверить | Статус |
|------|----------------|--------|
| Запуск compose | `docker compose up --build` | ✅ |
| Dashboard: открытие | http://localhost:8080 | ✅ |
| Dashboard: сохранение Telegram (token, pairing, user ids) | Сохранить, перезапуск telegram-adapter | ✅ |
| Dashboard: проверка бота | Кнопка «Проверить бота» | ✅ |
| Dashboard: сохранение модели (URL, имя, LM Studio native) | Сохранить, перезапуск assistant-core | ✅ |
| Dashboard: проверка модели | Кнопка «Проверить подключение» | ✅ |
| Dashboard: MCP (добавить/удалить, args) | Добавить сервер с JSON args | ✅ |
| Dashboard: мониторинг Redis | Вкладка Мониторинг | ✅ |
| Pairing | Включить pairing, отправить /start боту | ✅ |
| Команды бота | /start, /help, /reasoning в меню | ✅ |
| Отправка сообщения в Telegram | Написать боту | ✅ |
| Стриминг ответа | Ответ появляется по частям, одно сообщение | ✅ |
| Индикатор «печатает» | sendChatAction(typing) при ответе | ✅ |
| Скрытие <think> | В ответе нет блока <think>...</think> | ✅ |
| LM Studio native (только message) | Включить в dashboard, ответ без reasoning в чате | ✅ |
| Skills: filesystem | Запрос прочитать файл в workspace | ✅ |
| Skills: shell (whitelist) | Разрешённая команда vs запрещённая | ✅ |
| Skills: git | Запрос git status | ✅ |
| Memory | Несколько сообщений подряд — контекст сохраняется | ✅ |
| Rate limit | Много запросов подряд — ограничение | ✅ |
| Whitelist user ids | Сообщение от не из списка — игнор | ✅ |

**Рекомендация:** оформить этот список в `docs/ACCEPTANCE.md` и прогонять перед релизом.

---

## 4. Дизайн и архитектура

### 4.1 Ревью архитектуры

- **Плюсы:**
  - Чёткие слои: Channel → Bus → Orchestrator → Agents → Skills → Model.
  - Stateless агенты, состояние в Redis (task, config).
  - Один Event Bus (Redis pub/sub), типизированные события (Pydantic).
  - Разделение конфига: dashboard пишет в Redis, core/telegram читают при старте и при необходимости.
- **Риски и улучшения:**
  - **Масштабирование assistant-core:** при `scale agent=3` сейчас три одинаковых процесса; все подписаны на одни каналы — возможны дубликаты обработки. Нужна одна очередь на задачу (один consumer на task_id) или распределение по очередям (например, по chat_id). Рекомендация: либо один worker для assistant-core, либо ввести очередь задач с блокировкой по task_id (Redis lock).
  - **Telegram:** один процесс на инстанс; long polling блокирует. Для высокой нагрузки — несколько инстансов с разными offset или webhook.
  - **MCP:** сейчас заглушка; при реализации — вынести подключение к MCP-серверам в отдельный слой (конфиг из Redis, healthcheck, таймауты).

### 4.2 Агенты и каналы

- **Агенты:** AssistantAgent (модель + stream_callback), ToolAgent (skills + runner). Контекст передаётся через TaskContext; stream_callback инжектится оркестратором — связность приемлемая.
- **Каналы:** только Telegram; адаптер подписан на CH_OUTGOING и CH_STREAM. Для второго канала (CLI/REST) нужен ещё один адаптер с той же подпиской — архитектура позволяет.
- **Рекомендация:** описать в README контракт событий (IncomingMessage, OutgoingReply, StreamToken) для разработчиков новых каналов.

---

## 5. Заключение и приоритеты для исправлений

### Критично для поддержки

1. **Покрытие тестами ≥90%** и **coverage в CI** (см. раздел 6–7).
2. **Оформление приёмочных тестов** в `docs/ACCEPTANCE.md` и прогон перед релизом.
3. **Описание конфига** в README: Redis vs env vs YAML, порядок переопределения.

### Важно

4. **Mock MCP сервер** и тесты конфигурации MCP (URL, args) и, при реализации, вызова MCP.
5. **Ruff (или black) в CI** и по возможности pre-commit.
6. **Тесты стриминга и Telegram:** on_stream, on_outgoing, _strip_think_blocks с моками httpx и bus.

### Желательно

7. Реализация MCP adapter по конфигу из Redis (или отдельная задача).
8. Уточнение масштабирования assistant-core (очередь/блокировка по task_id).
9. CHANGELOG.md и теги версий.

---

## 6. Рефакторинг по фичам/блокам

- **Streaming:** вынести общий контракт «итератор токенов» в один модуль (например, `models/streaming.py`) с адаптерами OpenAI / LM Studio.
- **Dashboard:** разбить app.py на blueprint’ы или модули по секциям (telegram, model, mcp, monitor, api); вынести шаблоны в отдельные строки/файлы.
- **Telegram:** вынести _strip_think_blocks, _send_typing, логику stream_state в отдельные функции/класс для тестируемости.
- **Config store:** уже вынесен; при добавлении ключей — единый список констант (как MCP_SERVERS_KEY, PAIRING_MODE_KEY).
- **Skills:** базовый класс и registry уже единообразны; MCP при реализации — тот же интерфейс run(params) → dict.

---

## 7. Покрытие тестами и CI

### 7.1 Цель

- **Покрытие кода ≥90%** (pytest-cov).
- Локально: `pytest --cov=assistant --cov-report=html --cov-fail-under=90`.
- В CI: тот же порог; артефакт отчёта (html или xml) для просмотра.

### 7.2 Настройка coverage

- В `pyproject.toml`: добавить `[tool.coverage.run]` и `[tool.coverage.report]`, exclude тестов и при необходимости части dashboard (если сложно достичь 90% без E2E).
- В dev-зависимостях: `pytest-cov`.
- В CI: шаг Run tests с `--cov=assistant --cov-report=xml --cov-fail-under=90` (или 85 на первом этапе, с последующим повышением).

### 7.3 Тесты и покрытие в CI

- Все тесты в `assistant/tests` должны запускаться в CI (уже запускаются).
- Добавить job `coverage`: установка pytest-cov, запуск pytest с coverage, загрузка артефакта (coverage.xml или html).
- Опционально: сервис Redis в CI для тестов, которые сейчас скипаются, чтобы не снижать покрытие из-за skip.

---

## 8. Краткая сводка для исправления замечаний

| # | Действие | Файлы/места |
|---|----------|-------------|
| 1 | Включить pytest-cov, задать fail-under 90 (или 85), вынести исключения | pyproject.toml |
| 2 | CI: coverage job, артефакт отчёта | .github/workflows/ci.yml |
| 3 | Тесты: telegram (on_stream, on_outgoing, strip_think, typing) | assistant/tests/test_channels.py |
| 4 | Тесты: dashboard save-маршруты, MCP | assistant/tests/test_dashboard.py |
| 5 | Тесты: lm_studio (generate + stream SSE) | assistant/tests/test_lm_studio.py |
| 6 | Тесты: gateway с use_lm_studio_native, fallback | assistant/tests/test_models.py |
| 7 | Тесты: orchestrator stream_callback, StreamToken | assistant/tests/test_orchestrator.py |
| 8 | Mock MCP сервер + тесты MCP config | assistant/tests/mcp_mock_server.py, test_mcp_*.py |
| 9 | Ruff (или black) в CI | .github/workflows/ci.yml, pyproject.toml |
| 10 | Документ приёмочных тестов | docs/ACCEPTANCE.md |
| 11 | README: конфиг Redis/env/YAML, контракт событий | README.md, assistant/README.md |
| 12 | Рефакторинг: streaming contract, dashboard blueprints, telegram helpers | По плану в п. 6 |

После выполнения пунктов 1–2 и 3–8 покрытие и стабильность CI будут соответствовать целям; пункты 9–12 улучшат поддерживаемость и ясность для разработки и релизов.
