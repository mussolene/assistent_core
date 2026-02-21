"""Entry point for assistant-core: run Orchestrator and subscribe to Event Bus."""

from __future__ import annotations

import asyncio
import logging
import sys

from assistant.config import get_config
from assistant.core.logging_config import setup_logging

logger = logging.getLogger(__name__)


def main() -> None:
    setup_logging()
    config = get_config()
    if not config.redis.url:
        logger.error("REDIS_URL is required")
        sys.exit(1)
    asyncio.run(run_core(config))


async def run_core(config: "assistant.config.loader.Config") -> None:
    from assistant.core.bus import EventBus
    from assistant.core.orchestrator import Orchestrator
    from assistant.core.agent_registry import AgentRegistry
    from assistant.memory.manager import MemoryManager
    from assistant.models.gateway import ModelGateway
    from assistant.skills.registry import SkillRegistry
    from assistant.skills.runner import SandboxRunner
    from assistant.skills.filesystem import FilesystemSkill
    from assistant.skills.shell import ShellSkill
    from assistant.skills.git import GitSkill
    from assistant.skills.vector_rag import VectorRagSkill
    from assistant.skills.mcp_adapter import McpAdapterSkill
    from assistant.agents.assistant import AssistantAgent
    from assistant.agents.tool_agent import ToolAgent

    bus = EventBus(config.redis.url)
    memory = MemoryManager(
        config.redis.url,
        short_term_window=config.memory.short_term_window,
        summary_threshold_messages=config.memory.summary_threshold_messages,
        vector_top_k=config.memory.vector_top_k,
        vector_collection=config.memory.vector_collection,
    )
    await memory.connect()

    from assistant.dashboard.config_store import get_config_from_redis
    redis_cfg = await get_config_from_redis(config.redis.url)
    openai_base_url = (redis_cfg.get("OPENAI_BASE_URL") or "").strip() or config.model.openai_base_url
    model_name = (redis_cfg.get("MODEL_NAME") or "").strip() or config.model.name
    fallback_name = (redis_cfg.get("MODEL_FALLBACK_NAME") or "").strip() or config.model.fallback_name
    cloud_fallback = (redis_cfg.get("CLOUD_FALLBACK_ENABLED") or "").lower() in ("true", "1", "yes")
    openai_api_key = (redis_cfg.get("OPENAI_API_KEY") or "").strip() or config.model.openai_api_key or "ollama"
    use_lm_studio_native = (redis_cfg.get("LM_STUDIO_NATIVE") or "").lower() in ("true", "1", "yes")

    model_gateway = ModelGateway(
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
    skills.register(ShellSkill(
        allowed_commands=config.security.command_whitelist,
        workspace_dir=config.sandbox.workspace_dir,
        cpu_limit_seconds=config.sandbox.cpu_limit_seconds,
        memory_limit_mb=config.sandbox.memory_limit_mb,
        network_enabled=config.sandbox.network_enabled,
    ))
    skills.register(GitSkill(
        workspace_dir=config.sandbox.workspace_dir,
        cpu_limit_seconds=config.sandbox.cpu_limit_seconds,
        memory_limit_mb=config.sandbox.memory_limit_mb,
        network_enabled=config.sandbox.network_enabled,
    ))
    skills.register(VectorRagSkill(memory.get_vector()))
    skills.register(McpAdapterSkill())
    runner = SandboxRunner()
    agent_registry = AgentRegistry()
    agent_registry.register("assistant", AssistantAgent(model_gateway, memory))
    agent_registry.register("tool", ToolAgent(skills, runner, memory))
    orchestrator = Orchestrator(config=config, bus=bus)
    orchestrator.set_agent_registry(agent_registry)
    await orchestrator.start()
    try:
        await orchestrator.run_forever()
    finally:
        await orchestrator.stop()


if __name__ == "__main__":
    main()
