"""Tests for IndexRepoSkill (iteration 7.1)."""

from unittest.mock import patch

import pytest

from assistant.skills.index_repo_skill import IndexRepoSkill


@pytest.fixture
def skill():
    return IndexRepoSkill(redis_url="redis://localhost:6379/0")


@pytest.mark.asyncio
async def test_index_repo_missing_repo_dir(skill):
    out = await skill.run({"user_id": "u1"})
    assert out.get("ok") is False
    assert "repo_dir" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_index_repo_no_qdrant_configured(skill, tmp_path):
    (tmp_path / "x.py").write_text("pass")
    with patch("assistant.skills.index_repo_skill.get_qdrant_url", return_value=""):
        out = await skill.run({"repo_dir": str(tmp_path), "user_id": "u1"})
    assert out.get("ok") is False
    assert "Qdrant" in out.get("error", "")


@pytest.mark.asyncio
async def test_index_repo_success(skill, tmp_path):
    (tmp_path / "a.py").write_text("x = 1")
    with patch("assistant.skills.index_repo_skill.get_qdrant_url", return_value="http://qdrant:6333"):
        with patch(
            "assistant.skills.index_repo_skill.index_repo_to_qdrant",
            return_value=(5, 1, ""),
        ):
            out = await skill.run({"repo_dir": str(tmp_path), "user_id": "u1"})
    assert out.get("ok") is True
    assert out.get("chunks_indexed") == 5
    assert out.get("files_count") == 1
    assert out.get("collection") == "repos"


@pytest.mark.asyncio
async def test_index_repo_custom_collection(skill, tmp_path):
    (tmp_path / "b.txt").write_text("hi")
    with patch("assistant.skills.index_repo_skill.get_qdrant_url", return_value="http://q:6333"):
        with patch(
            "assistant.skills.index_repo_skill.index_repo_to_qdrant",
            return_value=(1, 1, ""),
        ) as mock_index:
            out = await skill.run({
                "repo_dir": str(tmp_path),
                "user_id": "u1",
                "collection": "my_repos",
            })
    assert out.get("ok") is True
    assert out.get("collection") == "my_repos"
    mock_index.assert_called_once()
    assert mock_index.call_args[1]["collection"] == "my_repos"


@pytest.mark.asyncio
async def test_index_repo_skill_name():
    skill = IndexRepoSkill(redis_url="")
    assert skill.name == "index_repo"
