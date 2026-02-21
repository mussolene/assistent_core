# Modular Personal AI Assistant

Production-ready modular AI assistant with Telegram, pluggable skills, local/cloud models, and Docker deployment.

## System Overview

- **Local-first**: Ollama or OpenAI-compatible local API by default; cloud fallback optional.
- **Layered architecture**: Channel → Event Bus (Redis) → Orchestrator → Agents → Skills → Model Gateway.
- **Stateless agents**: AssistantAgent and ToolAgent; all state in Redis.
- **Skills**: Sandboxed filesystem, whitelisted shell, git, vector RAG, **tasks** (personal tasks with dates, documents, links, reminders; user-scoped in Redis), MCP adapter (stub).
- **Security**: Non-root containers, resource limits, command whitelist, audit logging, user whitelist, rate limiting.

## Architecture (ASCII)

```
Telegram (or other channel)
        |
        v
Telegram Adapter (long poll, whitelist, rate limit)
        |
        v
Redis (Event Bus + task state)
        |
        v
Orchestrator (state machine, no LLM in lifecycle)
        |
        v
Agent Registry --> AssistantAgent --> Model Gateway (Ollama / OpenAI)
        |
        v
        ToolAgent --> Skill Registry --> filesystem, shell, git, vector_rag, tasks, mcp
```

## Requirements

- Docker and Docker Compose
- Python 3.11+ (for local run)
- Redis (included in Compose)
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather)) — задаётся через Web Dashboard, не требуется при запуске.
- Local model: [Ollama](https://ollama.ai) (or any OpenAI-compatible API)

## Installation

1. Clone the repository and enter the project root (parent of `assistant/`).

2. Optional: create `.env` for overrides (Redis URL, OpenAI base URL). Токен бота задаётся через Web Dashboard.

3. Install a local model (e.g. Ollama):
   ```bash
   ollama pull llama3.2
   ```

## Running with Docker

From the project root (where `docker-compose.yml` is):

```bash
docker compose up --build
```

This starts:

- **redis**: Event bus and task state (with healthcheck).
- **dashboard**: Web UI on http://localhost:8080 — Telegram (token, pairing), Model (URL, test connection), MCP skills, Redis monitoring. Config stored in Redis.
- **assistant-core**: Orchestrator, agents, skills, model gateway client.
- **telegram-adapter**: Long polling; reads token from Redis. Registers bot commands (/start, /help, /reasoning). Pairing: enable in Dashboard, then user sends /start to bot to be added to allowed list.
- Vector memory is in-process by default (`sentence-transformers` in dependencies); no separate vector-db service required.

**Setup:** open http://localhost:8080, set bot token and optionally enable Pairing (then send /start to the bot), set model URL and name, save. Restart telegram-adapter after token change, assistant-core after model change.

Ensure Ollama (or your OpenAI-compatible server) is running on the host; set `OPENAI_BASE_URL` in `.env` if needed (e.g. `http://host.docker.internal:11434/v1`).

## Running locally (without Docker)

1. Install dependencies:
   ```bash
   pip install -e .
   ```

2. Start Redis (e.g. `redis-server` or Docker: `docker run -p 6379:6379 redis:7-alpine`).

3. Start the core (orchestrator + event listener):
   ```bash
   REDIS_URL=redis://localhost:6379/0 python -m assistant.main
   ```

4. In another terminal, start the Telegram adapter:
   ```bash
   TELEGRAM_BOT_TOKEN=your_token REDIS_URL=redis://localhost:6379/0 python -m assistant.channels.telegram
   ```

5. Run Ollama (or your LLM API) and open your bot in Telegram.

## Model configuration

Edit `assistant/config/default.yaml` or use environment variables:

- **Local (default)**:
  - `OPENAI_BASE_URL`: e.g. `http://localhost:11434/v1`
  - `OPENAI_API_KEY`: `ollama` (or any placeholder for Ollama)
  - `model.name`: e.g. `llama3.2`

- **Cloud fallback** (off by default):
  - Set `CLOUD_FALLBACK_ENABLED=true` and `OPENAI_API_KEY=sk-...` to allow fallback to OpenAI-compatible API when local fails.

## Enabling autonomous mode

In `assistant/config/default.yaml`:

```yaml
orchestrator:
  autonomous_mode: true
  max_iterations: 5
  quality_threshold: 0.8
```

Or set `ORCHESTRATOR_AUTONOMOUS_MODE=true` in `.env`. When `autonomous_mode` is false, the assistant does at most one iteration (no tool loop).

## Memory: уровни и данные пользователя

- **Кратковременная (short-term)**: последние N сообщений в Redis; окно задаётся `memory.short_term_window`.
- **Векторная память** включена по умолчанию и имеет три уровня:
  - **Краткосрочная (short)**: до `memory.vector_short_max` записей (по умолчанию 100), хранится в `vector_persist_dir/short.json`.
  - **Среднесрочная (medium)**: до `memory.vector_medium_max` (500), `medium.json`.
  - **Долговременная (long)**: без лимита, `long.json`.
- Очистка векторной памяти: `memory.clear_vector(level)` — передать `"short"`, `"medium"`, `"long"` или `None` (очистить все уровни). Константы: `VECTOR_LEVEL_SHORT`, `VECTOR_LEVEL_MEDIUM`, `VECTOR_LEVEL_LONG` в `assistant.memory.manager`.
- **Данные о пользователе**: ключ–значение в Redis по `user_id` (профиль, таймзона, предпочтения). API: `get_user_data(user_id)`, `set_user_data(user_id, ...)`, `clear_user_data(user_id)`. Эти данные автоматически попадают в системный контекст при сборке сообщений для модели.

Настройки в `memory`: `vector_persist_dir`, `vector_short_max`, `vector_medium_max` (см. `assistant/config/default.yaml`).

## Adding a new skill

1. Implement a class in `assistant/skills/` that extends `BaseSkill` (see `assistant/skills/base.py`):
   - `name`: str
   - `async def run(self, params: dict) -> dict`

2. Register it in `assistant/main.py`:
   ```python
   from assistant.skills.my_skill import MySkill
   skills.register(MySkill(...))
   ```

3. Skills run through the sandbox runner (audit + optional subprocess limits). For shell-like skills use the existing command whitelist and `run_in_sandbox` in `assistant/security/sandbox.py`.

## Security

- **Containers**: Run as non-root (`user: "1000:1000"`), with CPU and memory limits.
- **Secrets**: Loaded from `.env`; never commit `.env`.
- **Telegram**: Optional user ID whitelist and per-user rate limiting.
- **Shell**: Only whitelisted commands (e.g. `git`, `pytest`, `ls`, `cat`, `python`); dangerous patterns (e.g. `rm -rf /`, arbitrary `curl`) are blocked.
- **Filesystem skill**: Restricted to a workspace directory; path traversal outside it is rejected.
- **Audit**: Structured audit log for skill runs and results; sensitive keys redacted.
- **Network**: Cloud and egress controlled by config; cloud disabled by default.

## Known limitations

- Streaming replies to Telegram are sent as a single message when done (no live token streaming in the UI).
- Vector memory uses an in-process store by default (sentence-transformers + JSON files for short/medium/long levels); optional Qdrant service is defined in Compose but not wired in code yet.
- MCP adapter is a stub and returns "not implemented".
- Scaling: `docker compose up --scale assistant-core=3` runs multiple core instances; all consume from the same Redis. Ensure only one process runs the orchestrator loop per task (current design uses a single core instance; for multi-worker you would need task claiming or a queue).

## License

MIT.
