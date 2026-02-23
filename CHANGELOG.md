# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

## [0.2.1] - 2025-02-22

### Added

- **Профиль minimal:** `sentence-transformers` вынесен в optional `vector`; установка по умолчанию без векторной памяти; `pip install .[vector]` для RAG ([ROADMAP_0.2.1](docs/ROADMAP_0.2.1.md)).
- **Уведомления MCP:** документация настройки Chat ID в README и MCP_DEV_SERVER; явное предупреждение в логах при пустом chat_id; в дашборде отображается текущий Chat ID для уведомлений; лог подписки telegram-adapter на `assistant:outgoing_reply`.
- **Ядро продукта:** документ [docs/CORE_PRODUCT.md](docs/CORE_PRODUCT.md) — определение ядра, опциональные модули, команды запуска минимального контура.
- **Полуформализация задач:** парсер короткой фразы `parse_task_phrase` (шаблоны: «завтра X», «послезавтра X», «высокий приоритет X», «X к ДД.ММ», «X на понедельник»); при создании задачи с `text`/`phrase` и без `title` парсер подставляет title и end_date.
- **Модель в дашборде:** URL и API key в одном блоке; кнопка «Загрузить модели», выбор модели из списка API (OpenAI /models, Ollama /api/tags); подстановка первой модели.

### Changed

- Версия пакета и MCP serverInfo: 0.2.1.

## [0.1.0] - Initial release

- Modular AI assistant: Telegram channel, Event Bus, Orchestrator, Assistant/Tool agents.
- Skills: filesystem, shell (whitelist), git, vector_rag, MCP adapter (stub).
- Model gateway: local (Ollama), LM Studio native, OpenAI-compatible cloud fallback.
- Dashboard for Telegram, model, MCP config; Redis monitor.
- Docker Compose deployment; sandbox and security settings.
