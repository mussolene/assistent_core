"""Restricted shell execution. Whitelist commands only."""

from __future__ import annotations

import logging
from typing import Any

from assistant.skills.base import BaseSkill
from assistant.security.command_whitelist import CommandWhitelist
from assistant.security.sandbox import run_in_sandbox

logger = logging.getLogger(__name__)


class ShellSkill(BaseSkill):
    """Execute whitelisted commands in sandbox. No rm -rf /, no arbitrary curl."""

    def __init__(
        self,
        allowed_commands: list[str],
        workspace_dir: str = "/workspace",
        cpu_limit_seconds: int = 30,
        memory_limit_mb: int = 256,
        network_enabled: bool = False,
    ) -> None:
        self._whitelist = CommandWhitelist(allowed_commands)
        self._workspace = workspace_dir
        self._cpu = cpu_limit_seconds
        self._memory = memory_limit_mb
        self._network = network_enabled

    @property
    def name(self) -> str:
        return "shell"

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        raw = params.get("command") or params.get("cmd") or ""
        if not raw.strip():
            return {"error": "command required", "ok": False}
        parsed = self._whitelist.parse_command(raw)
        if parsed is None:
            ok, reason = self._whitelist.is_allowed(raw)
            return {"error": reason or "command not allowed", "ok": False}
        cmd_list, _ = parsed
        code, stdout, stderr = await run_in_sandbox(
            cmd_list,
            cwd=self._workspace,
            cpu_limit_seconds=self._cpu,
            memory_limit_mb=self._memory,
            network=self._network,
        )
        return {
            "returncode": code,
            "stdout": stdout,
            "stderr": stderr,
            "ok": code == 0,
        }
