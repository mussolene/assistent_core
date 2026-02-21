# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added

- Test coverage for audit, logging_config, task_manager (with mocked Redis), memory (summary, manager get_context), integration test (orchestrator + assistant + stream/outgoing).
- Pre-commit config (ruff, optional pytest).

### Changed

- Coverage requirement raised to 90% (CI and pyproject.toml).

## [0.1.0] - Initial release

- Modular AI assistant: Telegram channel, Event Bus, Orchestrator, Assistant/Tool agents.
- Skills: filesystem, shell (whitelist), git, vector_rag, MCP adapter (stub).
- Model gateway: local (Ollama), LM Studio native, OpenAI-compatible cloud fallback.
- Dashboard for Telegram, model, MCP config; Redis monitor.
- Docker Compose deployment; sandbox and security settings.
