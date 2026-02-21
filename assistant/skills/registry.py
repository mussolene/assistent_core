"""Dynamic skill registry: name -> skill implementation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from assistant.skills.base import BaseSkill

if TYPE_CHECKING:
    from assistant.skills.runner import SandboxRunner

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Register and resolve skills by name."""

    def __init__(self) -> None:
        self._skills: dict[str, BaseSkill] = {}

    def register(self, skill: BaseSkill) -> None:
        self._skills[skill.name] = skill
        logger.debug("registered skill: %s", skill.name)

    def get(self, name: str) -> BaseSkill | None:
        return self._skills.get(name)

    def list_skills(self) -> list[str]:
        return list(self._skills.keys())

    async def run(self, name: str, params: dict[str, Any], sandbox_runner: SandboxRunner) -> dict[str, Any]:
        """Resolve skill by name and run inside sandbox_runner. Returns result dict."""
        skill = self.get(name)
        if not skill:
            return {"error": f"unknown skill: {name}", "ok": False}
        try:
            return await sandbox_runner.run_skill(skill, params)
        except Exception as e:
            logger.exception("skill %s failed: %s", name, e)
            return {"error": str(e), "ok": False}
