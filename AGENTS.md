
# 🎯 Цель проекта

Создать **модульного локального AI-ассистента** с:

* поддержкой локальных и облачных моделей
* инструментами (skills)
* масштабируемыми агентами
* каналами связи (Telegram первый)
* безопасной песочницей
* автономным циклом разработки (опционально)
* Docker-развёртыванием
* README с полной инструкцией

---

# 🔄 Workflow (привычки агента)

**⚠️ КОММИТЫ ОБЯЗАТЕЛЬНЫ:** после каждого изменения (логического блока правок) всегда выполнять коммит. Не завершать сессию и не переходить к следующей задаче без `git add` + `git commit` с осмысленным описанием. Репозиторий не должен оставаться с незакоммиченными изменениями после выполненной работы.

После каждой итерации изменений:

1. **Коммит обязателен** — по завершении логического блока: `git add` затронутые файлы, `git commit -m "описание"`. Без исключений.
2. **Push не делать** — в удалённый репозиторий не пушить по умолчанию. Push только по явной просьбе пользователя.
3. **Сообщение коммита** — точно описывает, что сделано (на русском или английском, в одном стиле).

Цикл итерации: **Аналитика → Разработка → Тестирование → Исправление → Приёмка → Аудит**. После приёмки — регрессия по проекту, затем **коммит**.

---

# 📢 Подтверждения и уведомления (канал Telegram)

* **Требовать подтверждение** — когда действие затрагивает пользователя или требует его решения (push, deploy, опасные команды, выбор варианта), использовать `ask_confirmation`: отправить запрос в Telegram с кнопками Подтвердить/Отклонить и дождаться ответа.
* **Уведомлять о завершении** — по окончании важного действия (задача выполнена, пайплайн завершён, ошибка) предупреждать пользователя через `notify` (сообщение в Telegram).
* **Продолжение работы** — если нужно продолжить (следующий шаг, уточнение, выбор), запрашивать у пользователя через тот же канал (ask_confirmation или вопрос в чат).
* **Таймаут запроса подтверждения** — время ожидания ответа на ask_confirmation: **120 секунд** по умолчанию (настраивается в коде/конфиге).

Полная разбивка фич по итерациям: **docs/ITERATIONS_ROADMAP.md** (поиск репо, авторизация дашборда, Qdrant/документы, email, настройки через бота, список репо, индексация репо в Qdrant, память разговоров).

---

# 🔌 MCP проекта (не забывать)

В текущем проекте доступен **MCP**, через который агент может вызывать функции в канале Telegram. **К этому списку не возвращаться** — использовать по необходимости.

| Инструмент | Назначение |
|------------|------------|
| **notify** | Отправить сообщение в Telegram. Аргумент: `message` (строка). Поддерживает разметку: в чат уходит текст, адаптер конвертирует Markdown → Telegram HTML (**жирный**, *курсив*, `код`). |
| **ask_confirmation** | Запросить подтверждение у пользователя: сообщение в Telegram с кнопками Подтвердить/Отклонить. Аргументы: `message`, опционально `timeout_sec` (по умолчанию 120). Использовать перед действиями (push, deploy, опасные команды). |
| **get_user_feedback** | Забрать накопленные сообщения от пользователя (отправленные через `/dev ...` в Telegram). Без аргументов. |

Подключение: HTTP API дашборда (см. **docs/MCP_DEV_SERVER.md**), либо stdio `python -m assistant.mcp_server`. Для проверки отображения Markdown в чате — вызвать **notify** с текстом, содержащим `**жирный**`, `*курсив*`, `` `код` ``.

---

# 🏗 Архитектурная модель

## 1️⃣ Общая схема

```text
Channel Layer
   ↓
Channel Adapter
   ↓
Event Bus
   ↓
Orchestrator
   ↓
Agent Workers
   ↓
Skill Layer
   ↓
Model Gateway
```

---

# 🧩 Модули проекта

## 1. Core

* Orchestrator (state machine)
* Task Manager
* Agent Registry
* Skill Registry
* Memory Manager
* Security Manager

---

## 2. Agents

Базовый интерфейс:

```python
class BaseAgent:
    def handle(task_context) -> AgentResult
```

Минимальный набор:

* AssistantAgent (основной)
* ToolAgent
* DevAgent (опционально)
* TestAgent (опционально)

Агенты stateless.
Контекст хранится централизованно.

---

## 3. Skills (расширяемые)

Каждый skill — отдельный адаптер:

* filesystem (sandboxed)
* shell (ограниченный whitelist)
* git
* search
* vector-rag
* telegram_api
* mcp_adapter

Skill registry динамический.

---

## 4. Model Gateway

Поддержка:

* Local (Ollama / llama.cpp)
* OpenAI-compatible
* fallback
* streaming
* reasoning mode

Единый интерфейс:

```python
generate(prompt, stream=False, reasoning=False)
```

---

## 5. Channel Layer

Реализован: **Telegram** (long polling, streaming, whitelist, pairing).

В событиях задан тип канала (`ChannelKind`); оркестратор и адаптеры маршрутизируют по нему. Готовы к добавлению: **Slack**, **Web** (чат в браузере), **Email**; iMessage/WhatsApp — через мосты или официальные API (ограничения см. в документе).

Требования к каналу:

* streaming ответа
* режим reasoning (отдельный флаг)
* логирование действий
* multi-user support
* ACL

**Расширение каналов и фронтенд:** см. [docs/CHANNELS_AND_FRONTEND.md](docs/CHANNELS_AND_FRONTEND.md) — как добавить Slack/Web/Email, варианты «красивого» фронта (улучшение Flask-дашборда или отдельное SPA).

---

# 🔐 Безопасность (обязательно)

## 1️⃣ Песочница агентов

* Docker sandbox
* no root
* resource limits (CPU / RAM)
* network isolation
* read-only FS по умолчанию

---

## 2️⃣ Ограничение shell

Whitelist:

```yaml
allowed_commands:
  - git
  - pytest
  - ls
  - cat
```

Запрет:

* rm -rf /
* curl внешних адресов (по умолчанию)
* произвольный exec

---

## 3️⃣ Приватность

* Локальные модели по умолчанию
* Облако отключено флагом
* Логи без сохранения чувствительных данных
* Шифрование токенов
* .env не коммитится

---

## 4️⃣ Контроль утечек

* Skill isolation
* Network egress control
* Redaction layer (опционально)
* API rate limiting
* Audit log

---

## 5️⃣ Telegram безопасность

* user_id whitelist
* rate limiting
* защита от prompt injection
* режим read-only при недоверенном источнике

---

# 🧠 Memory Architecture

Разделение:

* Short-term (последние N сообщений)
* Task Memory
* Compressed Memory
* Vector Memory

Контекст передается минимальный.

---

# 🔁 Автономный режим

Опциональный флаг:

```yaml
autonomous_mode: true
max_iterations: 5
quality_threshold: 0.8
```

Оркестратор:

* ограничивает цикл
* анализирует прогресс
* останавливает бесконечные попытки

---

# 🐳 Docker-структура

```yaml
services:
  assistant-core
  redis
  vector-db
  model-gateway
  telegram-adapter
```

Scaling:

```bash
docker compose up --scale agent=3
```

---

# 📁 Структура проекта

```text
assistant/
 ├── core/
 ├── agents/
 ├── skills/
 ├── channels/
 ├── models/
 ├── memory/
 ├── security/
 ├── config/
 ├── docker/
 ├── tests/
 └── README.md
```

---

# 📄 Язык документации

* **Основной язык** пользовательской и внутренней документации — **русский** (README, docs/, AGENTS.md).
* **Английский** допустим для CHANGELOG, Contributing, лицензии и метаданных (совместимость с GitHub, Keep a Changelog). При смешении в одном файле предпочтителен один основной язык с помеченным блоком при необходимости.

---

# 📘 README должен содержать

## 1. Установка

* Требования (Docker, Python 3.11+)
* Получение Telegram token
* Настройка .env
* Подключение модели

---

## 2. Запуск

```bash
docker compose up --build
```

---

## 3. Настройка модели

```yaml
model:
  provider: local
  name: mistral
  fallback: gpt-4
```

---

## 4. Включение автономного режима

---

## 5. Добавление нового skill

---

## 6. Безопасность

* sandbox описание
* ограничения
* рекомендации

---

# 🚀 Минимально рабочий результат (MVP)

После запуска:

* Telegram-бот отвечает
* поддерживает streaming
* может читать локальные файлы (sandbox)
* использует локальную модель
* не отправляет данные в облако
* логирует действия
* можно масштабировать агентов

Это уже полноценный рабочий ассистент.

---

# 📈 Масштабируемость

Добавление нового агента:

```yaml
agents:
  - type: research
  - type: coding
```

Оркестратор автоматически подключает.

---

# 🎯 Стратегически

Это:

* инструмент для личной продуктивности
* основа корпоративного решения
* фундамент для коммерческого продукта

Ты не строишь «чат».
Ты строишь инфраструктуру.

SYSTEM PROMPT
```
You are a senior distributed systems architect and security-focused AI infrastructure engineer.

Your task is to build a production-ready modular personal AI assistant system.

The result must be a fully working project that can be run immediately after cloning and following README instructions.

Do NOT generate partial code.
Do NOT leave TODO placeholders.
Do NOT simplify architecture.
Do NOT skip security hardening.
Do NOT ask the user for clarification.
Make reasonable engineering decisions when unspecified.

The system must satisfy the following requirements:

====================================================
1. CORE GOAL
====================================================

Build a modular, scalable, secure personal AI assistant with:

- Local-first architecture
- Pluggable model providers
- Agent-based internal design
- Telegram as the primary communication channel
- Streaming and reasoning modes
- Sandbox execution for tools
- Docker-based deployment
- Production-level README

The assistant must be usable immediately after startup.

====================================================
2. ARCHITECTURE REQUIREMENTS
====================================================

Use a modular layered architecture:

Channel Layer
→ Channel Adapter
→ Event Bus
→ Orchestrator
→ Agent Workers
→ Skill Layer
→ Model Gateway

The LLM must NOT control the system lifecycle.
The Orchestrator must be deterministic and state-driven.

Agents must be stateless.
All state must be stored centrally.

====================================================
3. REQUIRED MODULES
====================================================

Create the following directories:

assistant/
  core/
  agents/
  skills/
  channels/
  models/
  memory/
  security/
  config/
  docker/
  tests/
  README.md

====================================================
4. MODEL GATEWAY
====================================================

Support:

- Local models (Ollama or llama.cpp via OpenAI-compatible API)
- OpenAI-compatible cloud fallback
- Streaming responses
- Reasoning mode (flag-controlled)

Must include:

generate(prompt, stream=False, reasoning=False)

Cloud usage must be disabled by default.

====================================================
5. TELEGRAM CHANNEL
====================================================

Implement Telegram bot integration using secure long polling.

Features:
- Streaming token responses
- Reasoning mode toggle
- Multi-user support
- User ID whitelist
- Rate limiting
- Prompt injection protection

The bot must work immediately after token setup.

====================================================
6. SKILLS SYSTEM
====================================================

Implement pluggable skill system.

Required base skills:
- Sandboxed filesystem access
- Restricted shell execution (whitelist only)
- Git interaction
- Simple vector memory (local)
- MCP adapter interface

Skills must run inside a sandbox with:

- No root privileges
- CPU and memory limits
- Network isolation by default
- Explicit whitelist for outbound access

====================================================
7. SANDBOX & SECURITY
====================================================

Mandatory:

- Docker isolation
- Non-root containers
- Resource constraints
- Command whitelist
- No unrestricted exec
- Secrets loaded from .env
- .env excluded from git
- Structured audit logging
- Configurable cloud-disable flag
- Egress network control flag

Prevent:

- rm -rf /
- Arbitrary curl/wget
- Data exfiltration
- Prompt injection into system directives

====================================================
8. MEMORY MANAGEMENT
====================================================

Implement:

- Short-term memory (limited window)
- Task memory
- Compressed summary memory
- Simple vector memory (local embedding)

Only relevant context should be sent to the model.

Minimize token usage aggressively.

====================================================
9. AUTONOMOUS MODE (OPTIONAL BUT IMPLEMENTED)
====================================================

Support:

autonomous_mode: true/false
max_iterations: configurable
quality_threshold: configurable

Must prevent infinite loops.
Must stop after convergence or iteration cap.

====================================================
10. SCALABILITY
====================================================

Agents must be scalable via Docker Compose:

docker compose up --scale agent=3

Event-driven design required.
Use Redis (or equivalent lightweight event bus).

====================================================
11. DOCKER REQUIREMENTS
====================================================

Provide:

- docker-compose.yml
- Separate services:
  - assistant-core
  - redis
  - model-gateway
  - telegram-adapter
  - vector-db (lightweight)

All containers must:

- Run as non-root
- Have resource limits
- Use internal Docker network

====================================================
12. README REQUIREMENTS
====================================================

README must include:

- System overview
- Architecture diagram (ASCII acceptable)
- Installation requirements
- .env setup
- Telegram token setup
- Local model setup instructions
- Docker startup command
- How to enable/disable cloud fallback
- How to enable autonomous mode
- How to add new skill
- Security explanation
- Known limitations

README must be production quality.

====================================================
13. CODE QUALITY
====================================================

- Type hints required
- Structured logging
- No hardcoded secrets
- Clear separation of concerns
- Config-driven architecture (YAML or ENV)
- Clean dependency management
- Unit tests for core modules

====================================================
14. OUTPUT REQUIREMENT
====================================================

Generate:

- Full project structure
- All necessary code files
- Docker configuration
- Example .env template
- Production-grade README

The final result must be runnable immediately.

Do not summarize.
Do not explain.
Produce the full implementation.

Begin building the project now.
```