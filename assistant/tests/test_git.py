"""Tests for GitSkill: clone, read, commit, push, create_mr, subcommand with mocked sandbox."""

from unittest.mock import AsyncMock, patch

import pytest

from assistant.skills.git import GitSkill


@pytest.fixture
def skill_no_network():
    return GitSkill(workspace_dir="/w", network_enabled=False)


@pytest.fixture
def skill_with_network():
    return GitSkill(workspace_dir="/w", network_enabled=True)


@pytest.mark.asyncio
async def test_git_clone_missing_url(skill_with_network):
    out = await skill_with_network.run({"action": "clone"})
    assert out["ok"] is False
    assert "url" in out.get("error", "").lower() or "repo" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_git_clone_with_network(skill_with_network):
    with patch("assistant.skills.git.run_in_sandbox", new_callable=AsyncMock, return_value=(0, "stdout", "")):
        out = await skill_with_network.run({"action": "clone", "url": "https://github.com/o/r"})
    assert out["ok"] is True
    assert out.get("returncode") == 0


@pytest.mark.asyncio
async def test_git_clone_no_network_returns_error(skill_no_network):
    with patch("assistant.skills.git.run_in_sandbox", new_callable=AsyncMock, return_value=(1, "", "fatal: unable to access")):
        out = await skill_no_network.run({"action": "clone", "url": "https://github.com/o/r"})
    assert out["ok"] is False
    assert "network" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_git_read_missing_path(skill_no_network):
    out = await skill_no_network.run({"action": "read"})
    assert out["ok"] is False
    assert "path" in out.get("error", "").lower() or "file" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_git_read_success(skill_no_network):
    with patch("assistant.skills.git.run_in_sandbox", new_callable=AsyncMock, return_value=(0, "file content", "")):
        out = await skill_no_network.run({"action": "read", "path": "README.md"})
    assert out["ok"] is True
    assert out.get("content") == "file content"
    assert out.get("path") == "README.md"


@pytest.mark.asyncio
async def test_git_commit_missing_message(skill_no_network):
    out = await skill_no_network.run({"action": "commit", "paths": ["x"]})
    assert out["ok"] is False
    assert "message" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_git_commit_success(skill_no_network):
    with patch("assistant.skills.git.run_in_sandbox", new_callable=AsyncMock, side_effect=[(0, "", ""), (0, "1 file changed", "")]):
        out = await skill_no_network.run({"action": "commit", "message": "fix", "paths": ["a.txt"]})
    assert out["ok"] is True
    assert out.get("message") == "fix"


@pytest.mark.asyncio
async def test_git_push_missing_branch(skill_no_network):
    out = await skill_no_network.run({"action": "push"})
    assert out["ok"] is False
    assert "branch" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_git_push_no_network_error(skill_no_network):
    with patch("assistant.skills.git.run_in_sandbox", new_callable=AsyncMock, return_value=(1, "", "Could not resolve host")):
        out = await skill_no_network.run({"action": "push", "branch": "main"})
    assert out["ok"] is False
    assert "network" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_git_create_mr_delegates(skill_no_network):
    with patch("assistant.skills.git.create_merge_request", new_callable=AsyncMock, return_value={"ok": True, "url": "https://gitlab.com/...", "platform": "gitlab"}) as m:
        out = await skill_no_network.run({
            "action": "create_mr",
            "repo": "https://gitlab.com/o/r",
            "source_branch": "f",
            "target_branch": "main",
            "title": "T",
        })
    assert out["ok"] is True
    assert out.get("platform") == "gitlab"
    m.assert_called_once()


@pytest.mark.asyncio
async def test_git_status_subcommand(skill_no_network):
    with patch("assistant.skills.git.run_in_sandbox", new_callable=AsyncMock, return_value=(0, "On branch main", "")):
        out = await skill_no_network.run({"action": "status"})
    assert out["ok"] is True
    assert "stdout" in out
    assert "On branch main" in out["stdout"]


@pytest.mark.asyncio
async def test_git_list_repos_empty_workspace(skill_no_network):
    with patch("os.path.isdir", return_value=False):
        out = await skill_no_network.run({"action": "list_repos"})
    assert out["ok"] is True
    assert out.get("repos") == []


@pytest.mark.asyncio
async def test_git_list_repos_no_repos(skill_no_network):
    with patch("os.path.isdir", return_value=True), patch("os.listdir", return_value=[]):
        out = await skill_no_network.run({"action": "list_repos"})
    assert out["ok"] is True
    assert out.get("repos") == []


@pytest.mark.asyncio
async def test_git_list_repos_finds_repo(skill_no_network):
    with patch("os.path.isdir", return_value=True), patch("os.listdir", return_value=["my-repo"]), patch("os.path.exists", side_effect=lambda p: ".git" in p), patch("assistant.skills.git.run_in_sandbox", new_callable=AsyncMock, return_value=(0, "https://github.com/o/r", "")):
        out = await skill_no_network.run({"action": "list_repos"})
    assert out["ok"] is True
    assert len(out["repos"]) == 1
    assert out["repos"][0]["path"] == "my-repo"
    assert out["repos"][0]["remote_url"] == "https://github.com/o/r"


@pytest.mark.asyncio
async def test_git_list_cloned_alias(skill_no_network):
    with patch("os.path.isdir", return_value=False):
        out = await skill_no_network.run({"action": "list_cloned"})
    assert out["ok"] is True
    assert out.get("repos") == []


@pytest.mark.asyncio
async def test_git_name():
    s = GitSkill()
    assert s.name == "git"
