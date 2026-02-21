"""Git interaction via whitelisted git commands in sandbox."""

from __future__ import annotations

import logging
from typing import Any

from assistant.skills.base import BaseSkill
from assistant.security.command_whitelist import CommandWhitelist
from assistant.security.sandbox import run_in_sandbox

logger = logging.getLogger(__name__)

GIT_ALLOWED = ["git"]


class GitSkill(BaseSkill):
    """Run git subcommands (status, diff, log, etc.) in sandbox."""

    def __init__(
        self,
        workspace_dir: str = "/workspace",
        cpu_limit_seconds: int = 30,
        memory_limit_mb: int = 256,
        network_enabled: bool = False,
    ) -> None:
        self._whitelist = CommandWhitelist(GIT_ALLOWED)
        self._workspace = workspace_dir
        self._cpu = cpu_limit_seconds
        self._memory = memory_limit_mb
        self._network = network_enabled

    @property
    def name(self) -> str:
        return "git"

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        subcommand = params.get("subcommand") or params.get("action") or "status"
        args = params.get("args") or []
        if isinstance(args, str):
            args = args.split()
        raw = "git " + subcommand + " " + " ".join(args)
        ok, reason = self._whitelist.is_allowed(raw)
        if not ok:
            return {"error": reason, "ok": False}
        cmd = ["git", subcommand] + list(args)
        code, stdout, stderr = await run_in_sandbox(
            cmd,
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
