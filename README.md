# Assistant Core

<div align="center">

[![CI](https://github.com/mussolene/assistent_core/actions/workflows/ci.yml/badge.svg)](https://github.com/mussolene/assistent_core/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Coverage](https://img.shields.io/badge/coverage-90%25-brightgreen.svg)](https://github.com/mussolene/assistent_core/actions)
[![Docker](https://img.shields.io/badge/docker-ready-2496ed?logo=docker&logoColor=white)](https://www.docker.com/)
[![Documentation](https://img.shields.io/badge/docs-index-8ca1af.svg)](docs/README.md)

[![Version](https://img.shields.io/badge/version-0.1.0-informational)](https://github.com/mussolene/assistent_core)
[![Code size](https://img.shields.io/github/languages/code-size/mussolene/assistent_core)](https://github.com/mussolene/assistent_core)
[![Last commit](https://img.shields.io/github/last-commit/mussolene/assistent_core)](https://github.com/mussolene/assistent_core/commits/main)

</div>

Модульный персональный **AI-ассистент** с поддержкой Telegram, плагируемых навыков (skills), локальных и облачных моделей. Готов к развёртыванию через Docker.

---

## Содержание

- [Возможности](#возможности)
- [В цифрах](#в-цифрах)
- [Стек](#стек)
- [Быстрый старт](#быстрый-старт)
- [Архитектура](#архитектура)
- [Структура репозитория](#структура-репозитория)
- [Разработка и тесты](#разработка-и-тесты)
- [Документация](#документация)
- [Лицензия](#лицензия)

---

## Возможности

- **Локальные модели по умолчанию** — Ollama или любой OpenAI-совместимый API
- **Опциональное облако** — fallback на OpenAI-совместимый API при отключённой по умолчанию отправке в облако
- **Слоистая архитектура** — Channel → Event Bus (Redis) → Orchestrator → Agents → Skills → Model Gateway
- **Stateless-агенты** — AssistantAgent и ToolAgent, всё состояние в Redis
- **Навыки (skills)** — песочница для файловой системы, whitelist для shell, **git** (clone, read, commit, push, MR/PR через GitLab/GitHub API), vector RAG, **задачи (tasks)** — персональные задачи с датами, документами, ссылками и напоминаниями ([docs/TASKS_SKILL.md](docs/TASKS_SKILL.md)), заглушка MCP — см. [docs/GIT_SKILL.md](docs/GIT_SKILL.md)
- **Безопасность** — контейнеры без root, лимиты ресурсов, whitelist команд, аудит, whitelist пользователей Telegram, rate limiting
- **Web Dashboard** — настройка Telegram (токен, pairing), проверка подключения к модели, MCP-скиллы, мониторинг Redis
- **MCP-сервер для агента** — проект можно подключить в Cursor как MCP: уведомления в Telegram, запрос confirm/reject, обратная связь через `/dev` ([docs/MCP_DEV_SERVER.md](docs/MCP_DEV_SERVER.md))

## В цифрах

| Показатель | Значение |
|------------|----------|
| **Skills** | 6+ (filesystem, shell, git, vector_rag, tasks, mcp) |
| **Агенты** | 2 (Assistant, Tool) |
| **Сервисы Docker** | 4 (redis, dashboard, assistant-core, telegram-adapter) |
| **Покрытие тестами** | ≥90% |
| **Документов в docs/** | 18+ |
| **Python** | 3.11, 3.12 |

## Стек

| Компонент | Технология |
|-----------|------------|
| Язык | Python 3.11+ |
| Очереди / состояние | Redis |
| Канал | Telegram (long polling) |
| Модели | Ollama / OpenAI-совместимый API |
| Dashboard | Flask |
| Оркестрация | asyncio, Event Bus (pub/sub) |
| Линтер | Ruff |
| Тесты | pytest, pytest-cov |

## Быстрый старт

### Требования

- [Docker](https://docs.docker.com/get-docker/) и [Docker Compose](https://docs.docker.com/compose/install/)
- [Ollama](https://ollama.ai) (или другой OpenAI-совместимый сервер) — опционально на хосте
- Токен бота Telegram ([@BotFather](https://t.me/BotFather)) — задаётся через Dashboard

### Запуск

```bash
git clone https://github.com/YOUR_USERNAME/assistent_core.git
cd assistent_core
# Включите BuildKit для кэша pip (меньше скачиваний при пересборке)
export DOCKER_BUILDKIT=1
docker compose up --build
```

1. Откройте **http://localhost:8080** — Web Dashboard (вкладки: Telegram, Модель, MCP, Мониторинг).
2. В разделе Telegram введите токен бота; можно включить **Pairing** или сгенерировать код быстрой привязки. Сохраните.
3. В разделе Модель укажите URL API и имя модели, нажмите «Проверить подключение» и «Сохранить». Настройки модели применяются автоматически, перезапуск не нужен.
4. Запустите Ollama (или LM Studio) на хосте; при необходимости задайте в `.env` переменную `OPENAI_BASE_URL` (для Docker на Mac/Windows часто `http://host.docker.internal:11434/v1`). Если бот отвечает «Модель недоступна» — проверьте доступность URL из контейнера.

**Кэш при пересборке:** при `DOCKER_BUILDKIT=1` образы кэшируют слой установки зависимостей (`docker/requirements.txt`). После изменения только кода пересборка почти не скачивает пакеты. Файл `.dockerignore` уменьшает контекст сборки (исключены venv, .git, тесты и т.д.).

**Обновление кода без пересборки:** монтирование репо с хоста (volume) + `git pull` и перезапуск контейнеров; при необходимости — проверка каждые N минут через cron. Подробно: [docs/DOCKER_UPDATE_WITHOUT_REBUILD.md](docs/DOCKER_UPDATE_WITHOUT_REBUILD.md).

Подробная настройка и запуск без Docker — в [assistant/README.md](assistant/README.md).

### Конфигурация (приоритет)

Настройки берутся в порядке: **Redis (Dashboard)** → переменные окружения (`.env`) → YAML (`config/default.yaml`). Всё, что задаётся в Dashboard, хранится в Redis и переопределяет env/YAML при запуске core и telegram-adapter.

### Безопасность Dashboard (продакшен)

- **SECRET_KEY** — в продакшене обязательно задайте в `.env`: `SECRET_KEY=<случайная строка>`. Иначе сессии могут быть предсказуемы. При запуске без SECRET_KEY в логах выводится предупреждение.
- **HTTPS** — при работе через HTTPS задайте `HTTPS=1` или `FLASK_ENV=production`, чтобы cookie сессии передавались только по защищённому каналу (secure).

### Контракт событий (для новых каналов)

- **Вход:** адаптер публикует `IncomingMessage` (message_id, user_id, chat_id, text, reasoning_requested).
- **Выход:** подписка на канал `assistant:outgoing_reply` — payload `OutgoingReply` (task_id, chat_id, message_id, text, done).
- **Стриминг:** подписка на `assistant:stream_token` — payload `StreamToken` (task_id, chat_id, token, done). Токены дописываются в одно сообщение; при `done=True` или при приходе `OutgoingReply` с тем же task_id — финальное обновление.

## Архитектура

```
Telegram (или другой канал)
        │
        ▼
Telegram Adapter (long polling, whitelist, rate limit)
        │
        ▼
Redis (Event Bus + состояние задач)
        │
        ▼
Orchestrator (state machine, без LLM в жизненном цикле)
        │
        ▼
Agent Registry ──► AssistantAgent ──► Model Gateway (Ollama / OpenAI)
        │
        ▼
        ToolAgent ──► Skill Registry ──► filesystem, shell, git, vector_rag, tasks, mcp
```

## Структура репозитория

```
assistent_core/
├── README.md              # этот файл
├── LICENSE
├── .github/
│   └── workflows/
│       ├── ci.yml         # тесты
│       └── deploy.yml     # сборка и публикация Docker-образов
├── docker-compose.yml     # redis, dashboard, assistant-core, telegram-adapter
├── pyproject.toml
├── .env.example
└── assistant/
    ├── README.md          # детальная документация
    ├── config/            # конфигурация (YAML + env)
    ├── core/              # Event Bus, Orchestrator, Task Manager, Agent Registry
    ├── agents/            # AssistantAgent, ToolAgent
    ├── skills/            # filesystem, shell, git, vector_rag, tasks, mcp_adapter
    ├── channels/          # Telegram-адаптер
    ├── models/            # Model Gateway (local / cloud)
    ├── memory/            # short-term, task, summary, vector
    ├── security/          # audit, sandbox, command whitelist
    ├── dashboard/         # Web UI для настройки
    ├── docker/            # Dockerfile для core, telegram, dashboard
    └── tests/             # pytest
```

## Разработка и тесты

```bash
# Зависимости (Python 3.11+)
pip install -e ".[dev]"

# Тесты
pytest assistant/tests -v

# С покрытием (цель ≥90%)
pytest assistant/tests -v --cov=assistant --cov-report=html --cov-fail-under=90
```

Тесты, требующие Redis, помечаются как skipped при его отсутствии.

- **Скилл «Задачи»:** [docs/TASKS_SKILL.md](docs/TASKS_SKILL.md) — создание, даты, документы/ссылки, напоминания, список в Telegram.
- **Аудит и план доработок:** [docs/AUDIT.md](docs/AUDIT.md)
- **Приёмочное тестирование:** [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md)
- **План развития (многопользовательский режим, спаривание Telegram, Dashboard, роли):** [docs/ROADMAP.md](docs/ROADMAP.md)
- **Повторный аудит (безопасность, юзабилити, риски после этапов A–C):** [docs/AUDIT_V2.md](docs/AUDIT_V2.md)

### Документация

Полный индекс — [docs/README.md](docs/README.md). Отдельные темы: [RAG и Qdrant](docs/RAG_QDRANT.md), [каналы и фронтенд](docs/CHANNELS_AND_FRONTEND.md), [авторизация дашборда](docs/DASHBOARD_AUTH.md), [канал Telegram](docs/TELEGRAM_CHANNEL.md), [выбор стека фронта/бэкенда](docs/FRONTEND_STACK.md) / [BACKEND_STACK](docs/BACKEND_STACK.md).

## Лицензия

Проект распространяется под лицензией [MIT](LICENSE).

## Contributing

1. Сделайте fork репозитория.
2. Создайте ветку для фичи или багфикса (`git checkout -b feature/your-feature`).
3. Закоммитьте изменения и запушьте ветку.
4. Откройте Pull Request в основную ветку.

При добавлении кода убедитесь, что тесты проходят: `pytest assistant/tests -v`.

**Если вы форкаете репозиторий:** замените `YOUR_USERNAME` в команде клонирования и в URL бейджей (сейчас `mussolene/assistent_core`) на свой GitHub-аккаунт или организацию.
