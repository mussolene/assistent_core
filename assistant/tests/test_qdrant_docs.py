"""Tests for Qdrant document pipeline (iteration 3.2): get_url, ensure_collection, upsert, index_document."""

from unittest.mock import MagicMock, patch

import httpx

from assistant.core import qdrant_docs


def test_get_qdrant_url_empty_without_env(monkeypatch):
    monkeypatch.delenv("QDRANT_URL", raising=False)
    with patch("assistant.dashboard.config_store.get_config_from_redis_sync", return_value={}):
        assert qdrant_docs.get_qdrant_url("redis://localhost/0") == ""


def test_get_qdrant_url_from_env(monkeypatch):
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    assert qdrant_docs.get_qdrant_url() == "http://qdrant:6333"
    assert qdrant_docs.get_qdrant_url("redis://x") == "http://qdrant:6333"


def test_get_qdrant_url_from_redis(monkeypatch):
    monkeypatch.delenv("QDRANT_URL", raising=False)
    with patch(
        "assistant.dashboard.config_store.get_config_from_redis_sync",
        return_value={"QDRANT_URL": "http://localhost:6333"},
    ):
        assert qdrant_docs.get_qdrant_url("redis://r") == "http://localhost:6333"


def test_ensure_collection_creates_when_404():
    get_resp = MagicMock()
    get_resp.status_code = 404
    put_resp = MagicMock()
    put_resp.status_code = 200
    with patch.object(httpx.Client, "get", return_value=get_resp):
        with patch.object(httpx.Client, "put", return_value=put_resp):
            with patch.object(httpx.Client, "close"):
                ok = qdrant_docs.ensure_collection("http://qdrant:6333", "documents", 384)
    assert ok is True


def test_ensure_collection_exists_returns_true():
    get_resp = MagicMock()
    get_resp.status_code = 200
    with patch.object(httpx.Client, "get", return_value=get_resp):
        with patch.object(httpx.Client, "close"):
            ok = qdrant_docs.ensure_collection("http://qdrant:6333", "documents", 384)
    assert ok is True


def test_upsert_points_success():
    with patch.object(httpx.Client, "put") as mock_put:
        mock_put.return_value = MagicMock(status_code=200)
        with patch.object(httpx.Client, "close"):
            ok = qdrant_docs.upsert_points(
                "http://qdrant:6333",
                "documents",
                ["id1"],
                [[0.1] * 384],
                [{"text": "chunk1"}],
            )
    assert ok is True
    call_json = mock_put.call_args[1]["json"]
    assert "points" in call_json
    assert len(call_json["points"]) == 1
    assert call_json["points"][0]["id"] == "id1"
    assert call_json["points"][0]["payload"]["text"] == "chunk1"


def test_upsert_points_empty_returns_false():
    assert qdrant_docs.upsert_points("http://x", "c", [], [], []) is False


def test_index_document_to_qdrant_no_file():
    count, err = qdrant_docs.index_document_to_qdrant(
        "/nonexistent/file.txt",
        "user1",
        "",
    )
    assert count == 0
    assert "не найден" in err or "Qdrant" in err or err


def test_index_document_to_qdrant_no_qdrant_url(tmp_path):
    (tmp_path / "a.txt").write_text("hello world")
    count, err = qdrant_docs.index_document_to_qdrant(
        str(tmp_path / "a.txt"),
        "user1",
        "",
    )
    assert count == 0
    assert "QDRANT" in err or "Qdrant" in err


def test_index_document_to_qdrant_success(tmp_path):
    (tmp_path / "doc.txt").write_text("Short text for one chunk.")
    with patch("assistant.core.qdrant_docs._embed_texts", return_value=[[0.1] * 384]):
        with patch("assistant.core.qdrant_docs.ensure_collection", return_value=True):
            with patch("assistant.core.qdrant_docs.upsert_points", return_value=True):
                count, err = qdrant_docs.index_document_to_qdrant(
                    str(tmp_path / "doc.txt"),
                    "user1",
                    "http://qdrant:6333",
                )
    assert err == ""
    assert count >= 1


def test_index_document_to_qdrant_embed_fn_called(tmp_path):
    (tmp_path / "x.txt").write_text("Some content here.")
    embed_calls = []

    def fake_embed(texts):
        embed_calls.append(texts)
        return [[0.0] * 384] * len(texts)

    with patch("assistant.core.qdrant_docs.ensure_collection", return_value=True):
        with patch("assistant.core.qdrant_docs.upsert_points", return_value=True):
            count, err = qdrant_docs.index_document_to_qdrant(
                str(tmp_path / "x.txt"),
                "u1",
                "http://qdrant:6333",
                embed_fn=fake_embed,
            )
    assert err == ""
    assert len(embed_calls) == 1
    assert len(embed_calls[0]) == count


# --- Итерация 7.1: index_repo_to_qdrant ---


def test_index_repo_to_qdrant_no_dir():
    chunks, files, err = qdrant_docs.index_repo_to_qdrant(
        "/nonexistent/repo",
        "http://qdrant:6333",
    )
    assert chunks == 0 and files == 0
    assert "Каталог" in err or "найден" in err


def test_index_repo_to_qdrant_no_qdrant_url(tmp_path):
    (tmp_path / "a.py").write_text("x = 1")
    chunks, files, err = qdrant_docs.index_repo_to_qdrant(str(tmp_path), "")
    assert chunks == 0 and files == 0
    assert "Qdrant" in err


def test_index_repo_to_qdrant_success(tmp_path):
    (tmp_path / "readme.md").write_text("Hello world. " * 100)

    def fake_embed(texts):
        return [[0.1] * 384] * len(texts)

    with patch("assistant.core.qdrant_docs._embed_texts", side_effect=fake_embed):
        with patch("assistant.core.qdrant_docs.ensure_collection", return_value=True):
            with patch("assistant.core.qdrant_docs.upsert_points", return_value=True):
                chunks, files, err = qdrant_docs.index_repo_to_qdrant(
                    str(tmp_path),
                    "http://qdrant:6333",
                )
    assert err == ""
    assert files == 1
    assert chunks >= 1


def test_get_repo_rev_empty_for_non_git(tmp_path):
    (tmp_path / "x.txt").write_text("a")
    assert qdrant_docs._get_repo_rev(tmp_path) == ""


def test_get_repo_rev_returns_short_sha(tmp_path):
    (tmp_path / ".git").mkdir()
    with patch("subprocess.run") as m:
        m.return_value = type("R", (), {"returncode": 0, "stdout": "abc123def456\n"})()
        out = qdrant_docs._get_repo_rev(tmp_path)
    assert out == "abc123def456"


# --- Итерация 7.2: search_qdrant, get_qdrant_collection ---


def test_get_qdrant_collection_default(monkeypatch):
    monkeypatch.delenv("QDRANT_REPOS_COLLECTION", raising=False)
    with patch("assistant.dashboard.config_store.get_config_from_redis_sync", return_value={}):
        assert qdrant_docs.get_qdrant_collection("redis://x", "QDRANT_REPOS_COLLECTION", "repos") == "repos"


def test_get_qdrant_collection_from_env(monkeypatch):
    monkeypatch.setenv("QDRANT_REPOS_COLLECTION", "my_repos")
    assert qdrant_docs.get_qdrant_collection(None, "QDRANT_REPOS_COLLECTION", "repos") == "my_repos"


def test_search_qdrant_empty_query():
    assert qdrant_docs.search_qdrant("http://q:6333", "repos", "") == []
    assert qdrant_docs.search_qdrant("", "repos", "x") == []


def test_search_qdrant_success():
    resp = MagicMock(
        status_code=200,
        json=lambda: {
            "result": [
                {"id": "1", "score": 0.9, "payload": {"text": "chunk one", "repo": "r1", "path": "a.py"}},
                {"id": "2", "score": 0.8, "payload": {"text": "chunk two", "repo": "r1", "path": "b.py"}},
            ]
        },
    )
    with patch("assistant.core.qdrant_docs._embed_texts", return_value=[[0.1] * 384]):
        with patch.object(httpx.Client, "post", return_value=resp):
            with patch.object(httpx.Client, "close"):
                out = qdrant_docs.search_qdrant("http://qdrant:6333", "repos", "test query", top_k=5)
    assert len(out) == 2
    assert out[0]["text"] == "chunk one"
    assert out[0]["payload"]["repo"] == "r1"
    assert out[0]["score"] == 0.9
