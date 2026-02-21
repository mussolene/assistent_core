# Assistant Core

[![CI](https://github.com/mussolene/assistent_core/actions/workflows/ci.yml/badge.svg)](https://github.com/mussolene/assistent_core/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Модульный персональный AI-ассистент с поддержкой Telegram, плагируемых навыков (skills), локальных и облачных моделей. Готов к развёртыванию через Docker.

## Возможности

- **Локальные модели по умолчанию** — Ollama или любой OpenAI-совместимый API
- **Опциональное облако** — fallback на OpenAI-совместимый API при отключённой по умолчанию отправке в облако
- **Слоистая архитектура** — Channel → Event Bus (Redis) → Orchestrator → Agents → Skills → Model Gateway
- **Stateless-агенты** — AssistantAgent и ToolAgent, всё состояние в Redis
- **Навыки (skills)** — песочница для файловой системы, whitelist для shell, git, vector RAG, заглушка MCP
- **Безопасность** — контейнеры без root, лимиты ресурсов, whitelist команд, аудит, whitelist пользователей Telegram, rate limiting
- **Web Dashboard** — первичная настройка (токен бота, список пользователей) без запроса секретов при запуске

## Быстрый старт

### Требования

- [Docker](https://docs.docker.com/get-docker/) и [Docker Compose](https://docs.docker.com/compose/install/)
- [Ollama](https://ollama.ai) (или другой OpenAI-совместимый сервер) — опционально на хосте
- Токен бота Telegram ([@BotFather](https://t.me/BotFather)) — задаётся через Dashboard

### Запуск

```bash
git clone https://github.com/YOUR_USERNAME/assistent_core.git
cd assistent_core
docker compose up --build
```

1. Откройте **http://localhost:8080** — Web Dashboard.
2. Введите токен бота и при необходимости список разрешённых User ID, нажмите «Сохранить».
3. Перезапустите Telegram-адаптер: `docker compose restart telegram-adapter`.
4. Запустите Ollama на хосте (при необходимости укажите `OPENAI_BASE_URL` в `.env`, например `http://host.docker.internal:11434/v1`).

Подробная настройка и запуск без Docker — в [assistant/README.md](assistant/README.md).

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
        ToolAgent ──► Skill Registry ──► filesystem, shell, git, vector_rag, mcp
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
├── docker-compose.yml     # redis, dashboard, assistant-core, telegram-adapter, vector-db
├── pyproject.toml
├── .env.example
└── assistant/
    ├── README.md          # детальная документация
    ├── config/            # конфигурация (YAML + env)
    ├── core/              # Event Bus, Orchestrator, Task Manager, Agent Registry
    ├── agents/            # AssistantAgent, ToolAgent
    ├── skills/            # filesystem, shell, git, vector_rag, mcp_adapter
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
```

Тесты, требующие Redis, помечаются как skipped при его отсутствии.

## Лицензия

Проект распространяется под лицензией [MIT](LICENSE).

## Contributing

1. Сделайте fork репозитория.
2. Создайте ветку для фичи или багфикса (`git checkout -b feature/your-feature`).
3. Закоммитьте изменения и запушьте ветку.
4. Откройте Pull Request в основную ветку.

При добавлении кода убедитесь, что тесты проходят: `pytest assistant/tests -v`.

**Если вы форкаете репозиторий:** замените `YOUR_USERNAME` в README (бейдж CI и ссылка для клонирования) на имя вашего GitHub-аккаунта или организации.
