"""Tests for file indexing: chunking, extraction, file ref store, archives."""

import gzip
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from assistant.core import file_indexing as fi


def test_strip_html():
    assert fi._strip_html("<p>Hello</p>") == "Hello"
    assert fi._strip_html("<a href='x'>Link</a> text") == "Link text"
    assert "Hello" in fi._strip_html("<div>Hello <b>World</b></div>")


def test_is_archive():
    assert fi._is_archive(".zip", "") is True
    assert fi._is_archive("x.tar.gz", "") is True
    assert fi._is_archive("x.7z", "") is True
    assert fi._is_archive("x.rar", "") is True
    assert fi._is_archive("x.txt", "") is False
    assert fi._is_archive("x", "application/zip") is True


def test_extract_text_csv(tmp_path):
    f = tmp_path / "d.csv"
    f.write_text("a,b,c\n1,2,3", encoding="utf-8")
    out = fi._extract_text(f, "text/csv", "d.csv")
    assert "a" in out and "1" in out


def test_extract_text_html(tmp_path):
    f = tmp_path / "p.html"
    f.write_text("<html><body><p>Hello</p></body></html>", encoding="utf-8")
    out = fi._extract_text(f, "text/html", "p.html")
    assert "Hello" in out


def test_extract_text_md(tmp_path):
    f = tmp_path / "r.md"
    f.write_text("# Title\n\nBody text", encoding="utf-8")
    assert fi._extract_text(f, "", "r.md") == "# Title\n\nBody text"


def test_extract_text_xlsx(tmp_path):
    pytest.importorskip("openpyxl")
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws["A1"] = "Col1"
    ws["B1"] = "Col2"
    ws["A2"] = "a"
    ws["B2"] = "b"
    xlsx_path = tmp_path / "book.xlsx"
    wb.save(xlsx_path)
    out = fi._extract_text(xlsx_path, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "book.xlsx")
    assert "Col1" in out and "Col2" in out and "a" in out and "b" in out
    assert "Data" in out or "Лист" in out


def test_extract_content_from_file_txt(tmp_path):
    f = tmp_path / "t.txt"
    f.write_text("Plain text", encoding="utf-8")
    out = fi._extract_content_from_file(f, "text/plain", "t.txt")
    assert out == "Plain text"


def test_extract_content_from_file_zip_with_txt(tmp_path):
    zip_path = tmp_path / "a.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("inner.txt", "Content inside zip")
    out = fi._extract_content_from_file(zip_path, "application/zip", "a.zip")
    assert "Content inside zip" in out
    assert "inner.txt" in out


def test_extract_content_from_file_zip_path_traversal_ignored(tmp_path):
    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../../../etc/passwd", "skip")
    out = fi._extract_content_from_file(zip_path, "application/zip", "bad.zip")
    assert "skip" not in out


def test_extract_content_from_file_tar_with_txt(tmp_path):
    tar_path = tmp_path / "a.tar"
    with tarfile.open(tar_path, "w") as tf:
        inner = tmp_path / "inner.txt"
        inner.write_text("Content inside tar", encoding="utf-8")
        tf.add(inner, arcname="inner.txt")
    out = fi._extract_content_from_file(tar_path, "application/x-tar", "a.tar")
    assert "Content inside tar" in out
    assert "inner.txt" in out


def test_extract_content_from_file_single_gz(tmp_path):
    gz_path = tmp_path / "single.txt.gz"
    with gzip.open(gz_path, "wt", encoding="utf-8") as f:
        f.write("Gzipped text content")
    out = fi._extract_content_from_file(gz_path, "application/gzip", "single.txt.gz")
    assert "Gzipped text content" in out


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


def test_get_file_ref_missing():
    with patch("assistant.core.file_indexing._get_file_ref_sync", return_value=None):
        assert fi.get_file_ref("redis://localhost/0", "ref1") is None


def test_get_file_ref_found():
    with patch(
        "assistant.core.file_indexing._get_file_ref_sync",
        return_value={"file_id": "f1", "filename": "doc.pdf"},
    ):
        out = fi.get_file_ref("redis://localhost/0", "ref1")
        assert out == {"file_id": "f1", "filename": "doc.pdf"}


def test_list_file_refs_empty():
    with patch("assistant.core.file_indexing._list_file_refs_sync", return_value=[]):
        assert fi.list_file_refs("redis://localhost/0", "u1") == []


def test_list_file_refs_with_refs():
    with patch(
        "assistant.core.file_indexing._list_file_refs_sync",
        return_value=["r1", "r2"],
    ), patch(
        "assistant.core.file_indexing._get_file_ref_sync",
        side_effect=lambda _u, rid: {"filename": f"f_{rid}.txt"},
    ):
        out = fi.list_file_refs("redis://localhost/0", "u1")
        assert len(out) == 2
        assert out[0]["file_ref_id"] == "r1" and out[0]["filename"] == "f_r1.txt"
        assert out[1]["file_ref_id"] == "r2" and out[1]["filename"] == "f_r2.txt"
