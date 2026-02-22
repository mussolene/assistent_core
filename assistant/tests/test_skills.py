"""Tests for skills: registry, filesystem, shell whitelist, mcp stub, memory_control."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from assistant.memory.manager import (
    VECTOR_LEVEL_LONG,
    VECTOR_LEVEL_MEDIUM,
    VECTOR_LEVEL_SHORT,
)
from assistant.skills.checklist import ChecklistSkill
from assistant.skills.file_ref import FileRefSkill
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
async def test_registry_list_skills():
    reg = SkillRegistry()
    reg.register(ChecklistSkill())
    reg.register(McpAdapterSkill())
    assert set(reg.list_skills()) == {"checklist", "mcp_adapter"}
    assert reg.get("checklist") is not None
    assert reg.get("mcp_adapter") is not None


@pytest.mark.asyncio
async def test_registry_run_skill_raises():
    failing = MagicMock()
    failing.name = "failing"
    failing.run = AsyncMock(side_effect=RuntimeError("skill failed"))
    reg = SkillRegistry()
    reg.register(failing)
    result = await reg.run("failing", {}, SandboxRunner())
    assert result.get("ok") is False
    assert "skill failed" in result.get("error", "")


@pytest.mark.asyncio
async def test_runner_audit_called():
    skill = MagicMock()
    skill.name = "test_skill"
    skill.run = AsyncMock(return_value={"ok": True})
    with patch("assistant.skills.runner.audit") as audit_mock:
        result = await SandboxRunner().run_skill(skill, {"a": 1})
    assert result == {"ok": True}
    assert audit_mock.call_count >= 2
    assert any("skill_run" in str(c) for c in audit_mock.call_args_list)
    assert any("skill_result" in str(c) for c in audit_mock.call_args_list)


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
async def test_file_ref_list_empty():
    with patch("assistant.skills.file_ref.list_file_refs", return_value=[]):
        skill = FileRefSkill("redis://localhost:6379/99")
        out = await skill.run({"user_id": "u1", "action": "list"})
    assert out.get("ok") is True
    assert out.get("files") == []


@pytest.mark.asyncio
async def test_file_ref_send_without_ref_id():
    skill = FileRefSkill("redis://localhost:6379/99")
    out = await skill.run({"user_id": "u1", "action": "send"})
    assert out.get("ok") is False
    assert "file_ref_id" in out.get("error", "").lower() or "ref_id" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_file_ref_send_with_ref_id():
    with patch("assistant.skills.file_ref.get_file_ref", return_value={"file_id": "tg_file_123", "filename": "doc.pdf"}):
        skill = FileRefSkill("redis://localhost:6379/99")
        out = await skill.run({"user_id": "u1", "action": "send", "file_ref_id": "ref1"})
    assert out.get("ok") is True
    assert out.get("send_document") == {"file_id": "tg_file_123"}
    assert out.get("filename") == "doc.pdf"


@pytest.mark.asyncio
async def test_file_ref_send_ref_not_found():
    with patch("assistant.skills.file_ref.get_file_ref", return_value=None):
        skill = FileRefSkill("redis://localhost:6379/99")
        out = await skill.run({"user_id": "u1", "action": "send", "file_ref_id": "bad"})
    assert out.get("ok") is False
    assert "найден" in out.get("error", "") or "not found" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_file_ref_send_no_file_id_in_ref():
    with patch("assistant.skills.file_ref.get_file_ref", return_value={"filename": "x.txt"}):
        skill = FileRefSkill("redis://localhost:6379/99")
        out = await skill.run({"user_id": "u1", "action": "send", "file_ref_id": "r1"})
    assert out.get("ok") is False
    assert "file_id" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_file_ref_action_send_without_ref_id_returns_error():
    skill = FileRefSkill("redis://localhost:6379/99")
    out = await skill.run({"user_id": "u1", "action": "send"})
    assert out.get("ok") is False
    assert "file_ref_id" in out.get("error", "").lower() or "ref_id" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_file_ref_unknown_action():
    skill = FileRefSkill("redis://localhost:6379/99")
    out = await skill.run({"user_id": "u1", "action": "delete"})
    assert out.get("ok") is False
    assert "action" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_checklist_create():
    skill = ChecklistSkill()
    out = await skill.run(
        {"action": "create", "title": "День", "tasks": [{"text": "Утро"}, {"text": "Обед"}]}
    )
    assert out.get("ok") is True
    assert out.get("send_checklist") is not None
    assert out["send_checklist"]["title"] == "День"
    assert len(out["send_checklist"]["tasks"]) == 2
    assert out["send_checklist"]["tasks"][0]["text"] == "Утро"


@pytest.mark.asyncio
async def test_checklist_create_with_others_flags():
    skill = ChecklistSkill()
    out = await skill.run(
        {
            "action": "create",
            "title": "T",
            "tasks": [{"text": "One"}],
            "others_can_add_tasks": True,
            "others_can_mark_tasks_as_done": False,
        }
    )
    assert out.get("ok") is True
    assert out["send_checklist"].get("others_can_add_tasks") is True
    assert out["send_checklist"].get("others_can_mark_tasks_as_done") is False


@pytest.mark.asyncio
async def test_checklist_create_no_title():
    skill = ChecklistSkill()
    out = await skill.run({"action": "create", "tasks": [{"text": "X"}]})
    assert out.get("ok") is False
    assert "title" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_checklist_action_not_create():
    skill = ChecklistSkill()
    out = await skill.run({"action": "list", "title": "X", "tasks": [{"text": "Y"}]})
    assert out.get("ok") is False
    assert "create" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_checklist_tasks_not_list():
    skill = ChecklistSkill()
    out = await skill.run({"action": "create", "title": "T", "tasks": "not a list"})
    assert out.get("ok") is False
    assert "tasks" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_checklist_empty_tasks():
    skill = ChecklistSkill()
    out = await skill.run({"action": "create", "title": "T", "tasks": []})
    assert out.get("ok") is False
    assert "одна" in out.get("error", "") or "task" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_checklist_create_task_with_custom_id():
    """Tasks can have optional id; title truncated to 255."""
    skill = ChecklistSkill()
    out = await skill.run(
        {"action": "create", "title": "T", "tasks": [{"id": 100, "text": "Item"}]}
    )
    assert out.get("ok") is True
    assert out["send_checklist"]["tasks"][0]["id"] == 100
    assert out["send_checklist"]["tasks"][0]["text"] == "Item"


@pytest.mark.asyncio
async def test_checklist_create_tasks_as_strings():
    """Tasks can be plain strings (mapped to id i+1, text)."""
    skill = ChecklistSkill()
    out = await skill.run({"action": "create", "title": "List", "tasks": ["A", "B"]})
    assert out.get("ok") is True
    assert len(out["send_checklist"]["tasks"]) == 2
    assert out["send_checklist"]["tasks"][0]["text"] == "A"
    assert out["send_checklist"]["tasks"][1]["id"] == 2


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

    out = await skill.run({"action": "clear_vector", "level": "invalid", "user_id": "u1"})
    assert out["ok"] is False
    assert "level" in out.get("error", "").lower()

    out = await skill.run({"action": "clear_vector", "level": "medium", "user_id": "u1"})
    assert out["ok"] is True
    memory.clear_vector.assert_called_with(user_id="u1", level=VECTOR_LEVEL_MEDIUM)

    out = await skill.run({"action": "clear_vector", "level": "long", "user_id": "u1"})
    assert out["ok"] is True
    memory.clear_vector.assert_called_with(user_id="u1", level=VECTOR_LEVEL_LONG)

    out = await skill.run({"action": "reset_memory", "user_id": "u1", "scope": "bad_scope"})
    assert out["ok"] is False
    assert "scope" in out.get("error", "").lower()

    out = await skill.run({
        "action": "reset_memory",
        "user_id": "u1",
        "scope": "short_term",
        "session_id": "sess1",
    })
    assert out["ok"] is True
    memory.reset_memory.assert_called_with("u1", scope="short_term", session_id="sess1")

    out = await skill.run({"action": "unknown_action", "user_id": "u1"})
    assert out["ok"] is False
    assert "Неизвестное" in out.get("error", "") or "clear_vector" in out.get("error", "")
