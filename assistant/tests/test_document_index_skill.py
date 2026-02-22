"""Tests for DocumentIndexSkill (iteration 3.2)."""

from unittest.mock import patch

import pytest

from assistant.skills.document_index_skill import DocumentIndexSkill


@pytest.fixture
def skill():
    return DocumentIndexSkill(redis_url="redis://localhost:6379/0")


@pytest.mark.asyncio
async def test_index_document_missing_path(skill):
    out = await skill.run({"user_id": "u1"})
    assert out.get("ok") is False
    assert "path" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_index_document_no_qdrant_configured(skill, tmp_path):
    (tmp_path / "f.txt").write_text("hi")
    with patch("assistant.skills.document_index_skill.get_qdrant_url", return_value=""):
        out = await skill.run({"path": str(tmp_path / "f.txt"), "user_id": "u1"})
    assert out.get("ok") is False
    assert "Qdrant" in out.get("error", "")


@pytest.mark.asyncio
async def test_index_document_success(skill, tmp_path):
    (tmp_path / "doc.txt").write_text("Content for indexing.")
    with patch("assistant.skills.document_index_skill.get_qdrant_url", return_value="http://qdrant:6333"):
        with patch(
            "assistant.skills.document_index_skill.index_document_to_qdrant",
            return_value=(2, ""),
        ):
            out = await skill.run({"path": str(tmp_path / "doc.txt"), "user_id": "u1"})
    assert out.get("ok") is True
    assert out.get("chunks_indexed") == 2
    assert out.get("collection") == "documents"


@pytest.mark.asyncio
async def test_index_document_custom_collection(skill, tmp_path):
    (tmp_path / "x.txt").write_text("Text")
    with patch("assistant.skills.document_index_skill.get_qdrant_url", return_value="http://q:6333"):
        with patch(
            "assistant.skills.document_index_skill.index_document_to_qdrant",
            return_value=(1, ""),
        ) as mock_index:
            out = await skill.run({
                "path": str(tmp_path / "x.txt"),
                "user_id": "u1",
                "collection": "my_docs",
            })
    assert out.get("ok") is True
    assert out.get("collection") == "my_docs"
    mock_index.assert_called_once()
    call_kw = mock_index.call_args[1]
    assert call_kw["collection"] == "my_docs"


@pytest.mark.asyncio
async def test_index_document_skill_name():
    skill = DocumentIndexSkill(redis_url="")
    assert skill.name == "index_document"
