"""Tests for file indexing: chunking, extraction, file ref store."""

from pathlib import Path

import pytest

from assistant.core import file_indexing as fi


def test_chunk_text_empty():
    assert fi._chunk_text("") == []
    assert fi._chunk_text("   ") == []


def test_chunk_text_short():
    text = "Hello world."
    assert len(fi._chunk_text(text)) == 1
    assert fi._chunk_text(text)[0] == text


def test_chunk_text_splits_with_overlap():
    text = "a" * 600
    chunks = fi._chunk_text(text, chunk_size=200, overlap=50)
    assert len(chunks) >= 2
    assert all(len(c) <= 200 for c in chunks)


def test_extract_text_txt(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("Hello\nWorld", encoding="utf-8")
    assert fi._extract_text(f, "text/plain", "f.txt") == "Hello\nWorld"


def test_extract_text_image_returns_placeholder():
    # path can be any path; mime image -> placeholder
    p = Path("/nonexistent")
    assert "изображение" in fi._extract_text(p, "image/jpeg", "x.jpg")


@pytest.mark.asyncio
async def test_index_telegram_attachments_empty():
    ref_ids, text = await fi.index_telegram_attachments(
        "redis://localhost:6379/0",
        None,
        "u1",
        "c1",
        [],
        "",
    )
    assert ref_ids == []
    assert text == ""


@pytest.mark.asyncio
async def test_index_telegram_attachments_no_token():
    ref_ids, text = await fi.index_telegram_attachments(
        "redis://localhost:6379/0",
        None,
        "u1",
        "c1",
        [{"file_id": "x", "filename": "a.txt", "source": "telegram"}],
        "",
    )
    assert ref_ids == []
    assert text == ""
