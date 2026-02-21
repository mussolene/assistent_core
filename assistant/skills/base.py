"""Base interface for skills. All skills run inside the sandbox."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseSkill(ABC):
    """One skill: name and run(params) -> result dict."""

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        """Execute skill with given params. Returns dict with result or error."""
        pass
