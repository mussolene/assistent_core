"""Tests for skills: registry, filesystem, shell whitelist, mcp stub."""


import pytest

from assistant.skills.filesystem import FilesystemSkill
from assistant.skills.mcp_adapter import McpAdapterSkill
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
