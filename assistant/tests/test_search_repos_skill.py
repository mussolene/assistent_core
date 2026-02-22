"""Tests for SearchReposSkill (iteration 7.2)."""

from unittest.mock import patch

import pytest

from assistant.skills.search_repos_skill import SearchReposSkill


@pytest.fixture
def skill():
    return SearchReposSkill(redis_url="redis://localhost:6379/0")


@pytest.mark.asyncio
async def test_search_repos_missing_query(skill):
    out = await skill.run({"user_id": "u1"})
    assert out.get("ok") is False
    assert "query" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_search_repos_no_qdrant(skill):
    with patch("assistant.skills.search_repos_skill.get_qdrant_url", return_value=""):
        out = await skill.run({"query": "test", "user_id": "u1"})
    assert out.get("ok") is False
    assert "Qdrant" in out.get("error", "")


@pytest.mark.asyncio
async def test_search_repos_success(skill):
    with patch("assistant.skills.search_repos_skill.get_qdrant_url", return_value="http://qdrant:6333"):
        with patch(
            "assistant.skills.search_repos_skill.search_qdrant",
            return_value=[
                {"text": "hit 1", "payload": {"repo": "r1", "path": "a.py"}, "score": 0.95},
            ],
        ):
            out = await skill.run({"query": "function", "user_id": "u1"})
    assert out.get("ok") is True
    assert len(out.get("results", [])) == 1
    assert out["results"][0]["text"] == "hit 1"
    assert out.get("collection") == "repos"


@pytest.mark.asyncio
async def test_search_repos_custom_collection(skill):
    with patch("assistant.skills.search_repos_skill.get_qdrant_url", return_value="http://q:6333"):
        with patch("assistant.skills.search_repos_skill.search_qdrant", return_value=[]) as mock_search:
            out = await skill.run({"query": "x", "collection": "documents"})
    assert out.get("ok") is True
    assert out.get("collection") == "documents"
    mock_search.assert_called_once()
    assert mock_search.call_args[0][1] == "documents"


@pytest.mark.asyncio
async def test_search_repos_skill_name():
    skill = SearchReposSkill(redis_url="")
    assert skill.name == "search_repos"
