"""Sandbox runner: executes a skill with audit. Skills that need subprocess use run_in_sandbox internally."""

from __future__ import annotations

import logging
from typing import Any

from assistant.security.audit import audit
from assistant.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class SandboxRunner:
    """Run a skill and log the action. No extra isolation here; shell/git use security.sandbox."""

    async def run_skill(self, skill: BaseSkill, params: dict[str, Any]) -> dict[str, Any]:
        audit("skill_run", skill=skill.name, params_keys=list(params.keys()))
        result = await skill.run(params)
        audit("skill_result", skill=skill.name, ok=result.get("ok", False))
        return result
