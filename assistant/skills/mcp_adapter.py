"""MCP adapter interface. Stub for MVP: returns not implemented."""

from __future__ import annotations

from typing import Any

from assistant.skills.base import BaseSkill


class McpAdapterSkill(BaseSkill):
    """MCP server adapter. Not implemented in MVP."""

    @property
    def name(self) -> str:
        return "mcp_adapter"

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "error": "MCP adapter not implemented. Use filesystem, shell, git, or vector_rag.",
            "ok": False,
        }
