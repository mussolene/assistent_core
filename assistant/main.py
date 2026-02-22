"""Entry point for assistant-core: run Orchestrator and subscribe to Event Bus."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import TYPE_CHECKING

from assistant.config import get_config
from assistant.core.logging_config import setup_logging

if TYPE_CHECKING:
    from assistant.config.loader import Config

logger = logging.getLogger(__name__)


def main() -> None:
    setup_logging()
    config = get_config()
    if not config.redis.url:
        logger.error("REDIS_URL is required")
        sys.exit(1)
    asyncio.run(run_core(config))


async def run_core(config: Config) -> None:
    from assistant.agents.assistant import AssistantAgent
    from assistant.agents.tool_agent import ToolAgent
    from assistant.core.agent_registry import AgentRegistry
    from assistant.core.bus import EventBus
    from assistant.core.orchestrator import Orchestrator
    from assistant.memory.manager import MemoryManager
    from assistant.models.gateway import ModelGateway
    from assistant.skills.filesystem import FilesystemSkill
    from assistant.skills.git import GitSkill
    from assistant.skills.mcp_adapter import McpAdapterSkill
    from assistant.skills.memory_control import MemoryControlSkill
    from assistant.skills.registry import SkillRegistry
    from assistant.skills.runner import SandboxRunner
    from assistant.skills.shell import ShellSkill
    from assistant.skills.tasks import TaskSkill
    from assistant.skills.vector_rag import VectorRagSkill
    from assistant.skills.file_ref import FileRefSkill
    from assistant.skills.checklist import ChecklistSkill

    bus = EventBus(config.redis.url)
    memory = MemoryManager(
        config.redis.url,
        short_term_window=config.memory.short_term_window,
        summary_threshold_messages=config.memory.summary_threshold_messages,
        vector_top_k=config.memory.vector_top_k,
        vector_collection=config.memory.vector_collection,
        vector_persist_dir=config.memory.vector_persist_dir,
        vector_short_max=config.memory.vector_short_max,
        vector_medium_max=config.memory.vector_medium_max,
        vector_model_name=config.memory.vector_model_name,
        vector_model_path=config.memory.vector_model_path,
    )
    await memory.connect()

    from assistant.dashboard.config_store import get_config_from_redis, get_config_from_redis_sync

    # Токены и путь для Git: из Redis (дашборд) или env, чтобы поиск и клонирование работали
    redis_cfg = get_config_from_redis_sync(config.redis.url)
    if redis_cfg.get("GITHUB_TOKEN"):
        os.environ.setdefault("GITHUB_TOKEN", redis_cfg["GITHUB_TOKEN"])
    if redis_cfg.get("GITLAB_TOKEN"):
        os.environ.setdefault("GITLAB_TOKEN", redis_cfg["GITLAB_TOKEN"])
    git_workspace_dir = (
        (redis_cfg.get("GIT_WORKSPACE_DIR") or "").strip()
        or config.sandbox.git_workspace_dir
        or config.sandbox.workspace_dir
    )
    if not git_workspace_dir:
        git_workspace_dir = config.sandbox.workspace_dir

    async def get_gateway() -> ModelGateway:
        """Build gateway from current Redis config. Settings apply without restart."""
        redis_cfg = await get_config_from_redis(config.redis.url)
        openai_base_url = (
            redis_cfg.get("OPENAI_BASE_URL") or ""
        ).strip() or config.model.openai_base_url
        model_name = (redis_cfg.get("MODEL_NAME") or "").strip() or config.model.name
        fallback_name = (
            redis_cfg.get("MODEL_FALLBACK_NAME") or ""
        ).strip() or config.model.fallback_name
        cloud_fallback = (redis_cfg.get("CLOUD_FALLBACK_ENABLED") or "").lower() in (
            "true",
            "1",
            "yes",
        )
        openai_api_key = (
            (redis_cfg.get("OPENAI_API_KEY") or "").strip()
            or config.model.openai_api_key
            or "ollama"
        )
        use_lm_studio_native = (redis_cfg.get("LM_STUDIO_NATIVE") or "").lower() in (
            "true",
            "1",
            "yes",
        )
        # OpenAI-compat base URL must end with /v1 for chat/completions path
        if (
            not use_lm_studio_native
            and openai_base_url
            and not openai_base_url.rstrip("/").endswith("/v1")
        ):
            openai_base_url = openai_base_url.rstrip("/") + "/v1"
        return ModelGateway(
            provider=config.model.provider,
            model_name=model_name,
            fallback_name=fallback_name or None,
            cloud_fallback_enabled=cloud_fallback,
            reasoning_suffix=config.model.reasoning_model_suffix,
            openai_base_url=openai_base_url,
            openai_api_key=openai_api_key,
            use_lm_studio_native=use_lm_studio_native,
        )

    skills = SkillRegistry()
    skills.register(FilesystemSkill(workspace_dir=config.sandbox.workspace_dir))
    skills.register(
        ShellSkill(
            allowed_commands=config.security.command_whitelist,
            workspace_dir=config.sandbox.workspace_dir,
            cpu_limit_seconds=config.sandbox.cpu_limit_seconds,
            memory_limit_mb=config.sandbox.memory_limit_mb,
            network_enabled=config.sandbox.network_enabled,
        )
    )
    skills.register(
        GitSkill(
            workspace_dir=git_workspace_dir,
            cpu_limit_seconds=config.sandbox.cpu_limit_seconds,
            memory_limit_mb=config.sandbox.memory_limit_mb,
            network_enabled=config.sandbox.network_enabled,
        )
    )
    skills.register(VectorRagSkill(memory))
    skills.register(FileRefSkill(config.redis.url))
    skills.register(MemoryControlSkill(memory))
    skills.register(ChecklistSkill())
    skills.register(TaskSkill())
    skills.register(McpAdapterSkill())
    runner = SandboxRunner()
    agent_registry = AgentRegistry()
    agent_registry.register("assistant", AssistantAgent(gateway_factory=get_gateway, memory=memory))
    agent_registry.register("tool", ToolAgent(skills, runner, memory))
    orchestrator = Orchestrator(
        config=config, bus=bus, memory=memory, gateway_factory=get_gateway
    )
    orchestrator.set_agent_registry(agent_registry)
    await orchestrator.start()
    try:
        await orchestrator.run_forever()
    finally:
        await orchestrator.stop()


if __name__ == "__main__":
    main()
