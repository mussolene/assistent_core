"""Tests for skills: registry, filesystem, shell whitelist, mcp stub, memory_control."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from assistant.memory.manager import VECTOR_LEVEL_SHORT
from assistant.skills.filesystem import FilesystemSkill
from assistant.skills.mcp_adapter import McpAdapterSkill
from assistant.skills.memory_control import MemoryControlSkill
from assistant.skills.registry import SkillRegistry
from assistant.skills.runner import SandboxRunner


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "foo.txt").write_text("hello")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "bar.txt").write_text("world")
    return str(tmp_path)


@pytest.mark.asyncio
async def test_registry_get_unknown():
    reg = SkillRegistry()
    assert reg.get("nonexistent") is None
    result = await reg.run("nonexistent", {}, SandboxRunner())
    assert result.get("ok") is False
    assert "unknown" in result.get("error", "").lower()


@pytest.mark.asyncio
async def test_filesystem_read(workspace):
    skill = FilesystemSkill(workspace_dir=workspace)
    out = await skill.run({"action": "read", "path": "foo.txt"})
    assert out["ok"] is True
    assert out["content"] == "hello"


@pytest.mark.asyncio
async def test_filesystem_path_traversal(workspace):
    skill = FilesystemSkill(workspace_dir=workspace)
    out = await skill.run({"action": "read", "path": "../../etc/passwd"})
    assert out["ok"] is False
    assert "outside" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_filesystem_list(workspace):
    skill = FilesystemSkill(workspace_dir=workspace)
    out = await skill.run({"action": "list", "path": "."})
    assert out["ok"] is True
    assert "foo.txt" in out["entries"] or "sub" in out["entries"]


@pytest.mark.asyncio
async def test_mcp_stub():
    skill = McpAdapterSkill()
    out = await skill.run({})
    assert out["ok"] is False
    assert "not implemented" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_memory_control_clear_vector_and_reset_memory():
    """memory_control skill: clear_vector (all/short) and reset_memory (all) per user_id."""
    memory = MagicMock()
    memory.clear_vector = MagicMock()
    memory.reset_memory = AsyncMock()
    skill = MemoryControlSkill(memory)
    out = await skill.run({"action": "clear_vector", "level": "all", "user_id": "u1"})
    assert out["ok"] is True
    memory.clear_vector.assert_called_once_with(user_id="u1", level=None)
    out = await skill.run({"action": "clear_vector", "level": "short", "user_id": "u1"})
    assert out["ok"] is True
    memory.clear_vector.assert_called_with(user_id="u1", level=VECTOR_LEVEL_SHORT)
    out = await skill.run({"action": "reset_memory", "user_id": "u1", "scope": "all"})
    assert out["ok"] is True
    memory.reset_memory.assert_called_once_with("u1", scope="all", session_id="default")
    out = await skill.run({"action": "reset_memory"})
    assert out["ok"] is False
    assert "user_id" in out.get("error", "")
