"""Sandboxed filesystem access. Whitelist path under workspace only."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from assistant.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class FilesystemSkill(BaseSkill):
    """Read (and optional write) under workspace dir. No access outside."""

    def __init__(self, workspace_dir: str = "/workspace") -> None:
        self._root = Path(workspace_dir).resolve()

    @property
    def name(self) -> str:
        return "filesystem"

    def _safe_path(self, subpath: str) -> Path | None:
        p = (self._root / subpath.lstrip("/")).resolve()
        try:
            p.relative_to(self._root)
        except ValueError:
            return None
        return p

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        action = (params.get("action") or "read").lower()
        path = params.get("path") or params.get("file") or ""
        if not path:
            return {"error": "path required", "ok": False}
        safe = self._safe_path(path)
        if safe is None:
            return {"error": "path outside workspace", "ok": False}
        if action == "read":
            try:
                if not safe.exists():
                    return {"error": "file not found", "path": path, "ok": False}
                if safe.is_dir():
                    return {"error": "path is a directory", "path": path, "ok": False}
                text = safe.read_text(encoding="utf-8", errors="replace")
                return {"content": text, "path": path, "ok": True}
            except Exception as e:
                return {"error": str(e), "path": path, "ok": False}
        if action == "list":
            try:
                if not safe.exists():
                    return {"error": "path not found", "path": path, "ok": False}
                if not safe.is_dir():
                    return {"entries": [safe.name], "path": path, "ok": True}
                entries = [p.name for p in safe.iterdir()]
                return {"entries": entries, "path": path, "ok": True}
            except Exception as e:
                return {"error": str(e), "path": path, "ok": False}
        if action == "write":
            content = params.get("content", "")
            try:
                safe.parent.mkdir(parents=True, exist_ok=True)
                safe.write_text(content, encoding="utf-8")
                return {"path": path, "ok": True}
            except Exception as e:
                return {"error": str(e), "path": path, "ok": False}
        return {"error": f"unknown action: {action}", "ok": False}
