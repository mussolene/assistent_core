"""Pipeline: документ → чанки + embedding → upsert в Qdrant (итерация 3.2)."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Callable

import httpx

from assistant.core.file_indexing import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    _chunk_text,
    _extract_content_from_file,
)

logger = logging.getLogger(__name__)

DEFAULT_COLLECTION = "documents"
# all-MiniLM-L6-v2 dimension
DEFAULT_VECTOR_SIZE = 384


def get_qdrant_url(redis_url: str | None = None) -> str:
    """Qdrant URL из env или Redis (ключ QDRANT_URL). Пустая строка = отключено."""
    url = os.getenv("QDRANT_URL", "").strip()
    if url:
        return url.rstrip("/")
    if redis_url:
        try:
            from assistant.dashboard.config_store import get_config_from_redis_sync

            cfg = get_config_from_redis_sync(redis_url)
            url = (cfg.get("QDRANT_URL") or "").strip()
            if url:
                return url.rstrip("/")
        except Exception as e:
            logger.debug("get_qdrant_url from Redis: %s", e)
    return ""


def get_qdrant_collection(redis_url: str | None, key: str, default: str) -> str:
    """Имя коллекции из env (QDRANT_REPOS_COLLECTION / QDRANT_DOCUMENTS_COLLECTION) или Redis."""
    env_key = key if key.startswith("QDRANT_") else f"QDRANT_{key}"
    name = os.getenv(env_key, "").strip()
    if name:
        return name
    if redis_url:
        try:
            from assistant.dashboard.config_store import get_config_from_redis_sync

            cfg = get_config_from_redis_sync(redis_url)
            name = (cfg.get(env_key) or "").strip()
            if name:
                return name
        except Exception as e:
            logger.debug("get_qdrant_collection %s: %s", key, e)
    return default


def _embed_texts(texts: list[str], model_name: str = "all-MiniLM-L6-v2") -> list[list[float]]:
    """Эмбеддинг списка текстов через sentence-transformers. Возвращает список векторов."""
    if not texts:
        return []
    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(model_name)
        vectors = model.encode(texts)
        if hasattr(vectors, "tolist"):
            return [v.tolist() for v in vectors]
        return list(vectors)
    except Exception as e:
        logger.warning("embed_texts: %s", e)
        return []


def ensure_collection(
    base_url: str,
    collection: str,
    vector_size: int = DEFAULT_VECTOR_SIZE,
    client: httpx.Client | None = None,
) -> bool:
    """Создать коллекцию в Qdrant, если её нет. Возвращает True при успехе."""
    if not base_url:
        return False
    url = f"{base_url}/collections/{collection}"
    payload = {
        "vectors": {"size": vector_size, "distance": "Cosine"},
    }
    own = client is None
    if own:
        client = httpx.Client(timeout=10.0)
    try:
        r = client.get(url)
        if r.status_code == 200:
            return True
        if r.status_code == 404:
            r2 = client.put(url, json=payload)
            return r2.status_code in (200, 201)
        return False
    except Exception as e:
        logger.debug("ensure_collection %s: %s", collection, e)
        return False
    finally:
        if own and client:
            client.close()


def upsert_points(
    base_url: str,
    collection: str,
    ids: list[str],
    vectors: list[list[float]],
    payloads: list[dict[str, Any]],
    client: httpx.Client | None = None,
) -> bool:
    """Upsert точек в коллекцию Qdrant. ids/vectors/payloads — одинаковой длины."""
    if not base_url or not ids or len(ids) != len(vectors) or len(ids) != len(payloads):
        return False
    points = [
        {"id": id_, "vector": vec, "payload": pl}
        for id_, vec, pl in zip(ids, vectors, payloads)
    ]
    url = f"{base_url}/collections/{collection}/points"
    own = client is None
    if own:
        client = httpx.Client(timeout=30.0)
    try:
        r = client.put(url, json={"points": points})
        return 200 <= r.status_code < 300
    except Exception as e:
        logger.warning("upsert_points: %s", e)
        return False
    finally:
        if own and client:
            client.close()


def search_qdrant(
    base_url: str,
    collection: str,
    query: str,
    top_k: int = 5,
    embed_fn: Callable[[list[str]], list[list[float]]] | None = None,
    filter_conditions: dict[str, Any] | None = None,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """
    Поиск по коллекции Qdrant: эмбеддинг запроса, POST /points/search, возврат списка {text, payload, score}.
    filter_conditions: опционально {"must": [{"key": "user_id", "match": {"value": "u1"}}, ...]}.
    """
    if not base_url or not collection or not query or not query.strip():
        return []
    if embed_fn is None:
        vectors = _embed_texts([query.strip()])
    else:
        vectors = embed_fn([query.strip()])
    if not vectors:
        return []
    vector = vectors[0]
    url = f"{base_url}/collections/{collection}/points/search"
    payload = {"vector": vector, "limit": top_k, "with_payload": True}
    if filter_conditions:
        payload["filter"] = filter_conditions
    own = client is None
    if own:
        client = httpx.Client(timeout=15.0)
    try:
        r = client.post(url, json=payload)
        if r.status_code != 200:
            return []
        data = r.json()
        result = data.get("result") if isinstance(data, dict) else None
        if not isinstance(result, list):
            return []
        out: list[dict[str, Any]] = []
        for item in result:
            if not isinstance(item, dict):
                continue
            pl = item.get("payload") or {}
            score = item.get("score")
            text = pl.get("text", "")
            out.append({"text": text, "payload": pl, "score": score})
        return out
    except Exception as e:
        logger.debug("search_qdrant: %s", e)
        return []
    finally:
        if own and client:
            client.close()


def index_document_to_qdrant(
    file_path: str | Path,
    user_id: str,
    qdrant_url: str,
    collection: str = DEFAULT_COLLECTION,
    redis_url: str | None = None,
    mime_type: str = "",
    filename: str | None = None,
    embed_fn: Callable[[list[str]], list[list[float]]] | None = None,
) -> tuple[int, str]:
    """
    Извлечь текст из файла, разбить на чанки, эмбеддить, upsert в Qdrant.
    Возвращает (число проиндексированных чанков, сообщение об ошибке или "").
    """
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        return 0, "Файл не найден"
    if not qdrant_url:
        return 0, "Qdrant не настроен (QDRANT_URL)"
    name = filename or path.name
    text = _extract_content_from_file(path, mime_type, name)
    if not text or not text.strip():
        return 0, "Не удалось извлечь текст из файла"
    chunks = _chunk_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)
    if not chunks:
        return 0, "Нет чанков после разбиения"
    if embed_fn is None:
        vectors = _embed_texts(chunks)
    else:
        vectors = embed_fn(chunks)
    if len(vectors) != len(chunks):
        return 0, "Ошибка эмбеддинга"
    vector_size = len(vectors[0]) if vectors else DEFAULT_VECTOR_SIZE
    with httpx.Client(timeout=15.0) as client:
        if not ensure_collection(qdrant_url, collection, vector_size, client):
            return 0, "Не удалось создать или открыть коллекцию Qdrant"
        ids = [
            hashlib.sha256(f"{user_id}:{name}:{i}:{c[:50]}".encode()).hexdigest()[:24]
            for i, c in enumerate(chunks)
        ]
        payloads = [
            {
                "text": c,
                "user_id": user_id,
                "filename": name,
                "chunk_index": i,
                "source": "document",
            }
            for i, c in enumerate(chunks)
        ]
        if not upsert_points(qdrant_url, collection, ids, vectors, payloads, client):
            return 0, "Ошибка записи в Qdrant"
    return len(chunks), ""


# --- Итерация 7.1: индексация репозитория в Qdrant ---

REPO_COLLECTION = "repos"
REPO_MAX_FILES = 500
REPO_MAX_FILE_BYTES = 500_000
REPO_TEXT_SUFFIXES = (
    ".py", ".md", ".txt", ".rst", ".yaml", ".yml", ".json", ".html", ".htm",
    ".css", ".js", ".ts", ".sh", ".bat", ".csv", ".xml", ".toml", ".ini", ".cfg",
    ".sql", ".graphql", ".proto",
)
REPO_SKIP_DIRS = (".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".tox")


def _get_repo_rev(repo_dir: Path) -> str:
    """Версия/rev репо (git rev-parse HEAD) или пустая строка."""
    if not (repo_dir / ".git").exists():
        return ""
    try:
        import subprocess
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0 and r.stdout:
            return r.stdout.strip()[:16]
    except Exception as e:
        logger.debug("get_repo_rev: %s", e)
    return ""


def index_repo_to_qdrant(
    repo_dir: str | Path,
    qdrant_url: str,
    collection: str = REPO_COLLECTION,
    redis_url: str | None = None,
    repo_name: str | None = None,
    rev: str | None = None,
    user_id: str = "default",
    embed_fn: Callable[[list[str]], list[list[float]]] | None = None,
) -> tuple[int, int, str]:
    """
    Обход repo_dir, извлечение текста из файлов, чанки, embedding, upsert в Qdrant.
    Метаданные: repo, path, rev. Возвращает (всего чанков, число файлов, ошибка или "").
    """
    root = Path(repo_dir)
    if not root.exists() or not root.is_dir():
        return 0, 0, "Каталог не найден"
    if not qdrant_url:
        return 0, 0, "Qdrant не настроен (QDRANT_URL)"
    repo_label = repo_name or root.name
    rev = rev if rev is not None else _get_repo_rev(root)
    all_chunks: list[str] = []
    all_payloads: list[dict[str, Any]] = []
    files_done = 0
    for path in root.rglob("*"):
        if files_done >= REPO_MAX_FILES:
            break
        if path.is_dir() or any(skip in path.parts for skip in REPO_SKIP_DIRS):
            continue
        if path.suffix.lower() not in REPO_TEXT_SUFFIXES:
            continue
        try:
            if path.stat().st_size > REPO_MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        rel = path.relative_to(root)
        rel_str = str(rel).replace("\\", "/")
        text = _extract_content_from_file(path, "", path.name)
        if not text or not text.strip():
            continue
        chunks = _chunk_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)
        for i, c in enumerate(chunks):
            all_chunks.append(c)
            all_payloads.append({
                "text": c,
                "repo": repo_label,
                "path": rel_str,
                "rev": rev,
                "user_id": user_id,
                "chunk_index": i,
                "source": "repo",
            })
        files_done += 1
    if not all_chunks:
        return 0, 0, "Нет текстовых файлов или не удалось извлечь текст"
    if embed_fn is None:
        vectors = _embed_texts(all_chunks)
    else:
        vectors = embed_fn(all_chunks)
    if len(vectors) != len(all_chunks):
        return 0, 0, "Ошибка эмбеддинга"
    vector_size = len(vectors[0]) if vectors else DEFAULT_VECTOR_SIZE
    ids = [
        hashlib.sha256(f"repo:{repo_label}:{p.get('path','')}:{i}:{c[:50]}".encode()).hexdigest()[:24]
        for i, (c, p) in enumerate(zip(all_chunks, all_payloads))
    ]
    with httpx.Client(timeout=60.0) as client:
        if not ensure_collection(qdrant_url, collection, vector_size, client):
            return 0, 0, "Не удалось создать коллекцию Qdrant"
        if not upsert_points(qdrant_url, collection, ids, vectors, all_payloads, client):
            return 0, 0, "Ошибка записи в Qdrant"
    return len(all_chunks), files_done, ""


# --- Итерация 8.1: память разговоров в Qdrant ---

CONVERSATION_MEMORY_COLLECTION = "conversation_memory"


def index_conversation_to_qdrant(
    messages: list[dict[str, Any]],
    user_id: str,
    chat_id: str,
    qdrant_url: str,
    collection: str = CONVERSATION_MEMORY_COLLECTION,
    redis_url: str | None = None,
    embed_fn: Callable[[list[str]], list[list[float]]] | None = None,
) -> tuple[int, str]:
    """
    Индексация последних сообщений разговора в Qdrant (коллекция «conversation memory»).
    messages: [{"role": "user"|"assistant", "content": "..."}].
    Один вектор на сообщение; text = "role: content"; payload: user_id, chat_id, role, text.
    Возвращает (число точек, ошибка или "").
    """
    if not qdrant_url or not messages:
        return 0, ""
    collection_name = get_qdrant_collection(
        redis_url, "CONVERSATION_MEMORY_COLLECTION", collection
    )
    texts: list[str] = []
    payloads: list[dict[str, Any]] = []
    for i, msg in enumerate(messages):
        role = (msg.get("role") or "user").strip() or "user"
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        text = f"{role}: {content[:4000]}"
        texts.append(text)
        payloads.append({
            "text": text,
            "user_id": user_id,
            "chat_id": chat_id,
            "role": role,
            "source": "conversation_memory",
            "index": i,
        })
    if not texts:
        return 0, ""
    if embed_fn is None:
        vectors = _embed_texts(texts)
    else:
        vectors = embed_fn(texts)
    if len(vectors) != len(texts):
        return 0, "Ошибка эмбеддинга"
    vector_size = len(vectors[0]) if vectors else DEFAULT_VECTOR_SIZE
    ids = [
        hashlib.sha256(f"conv:{user_id}:{chat_id}:{i}:{t[:50]}".encode()).hexdigest()[:24]
        for i, t in enumerate(texts)
    ]
    with httpx.Client(timeout=30.0) as client:
        if not ensure_collection(qdrant_url, collection_name, vector_size, client):
            return 0, "Не удалось создать коллекцию Qdrant"
        if not upsert_points(qdrant_url, collection_name, ids, vectors, payloads, client):
            return 0, "Ошибка записи в Qdrant"
    return len(texts), ""


def search_conversation_memory(
    base_url: str,
    query: str,
    user_id: str,
    chat_id: str | None = None,
    collection: str = CONVERSATION_MEMORY_COLLECTION,
    top_k: int = 5,
    redis_url: str | None = None,
    embed_fn: Callable[[list[str]], list[list[float]]] | None = None,
) -> list[dict[str, Any]]:
    """
    Поиск по коллекции conversation_memory с фильтром по user_id и опционально chat_id.
    Возвращает список {text, payload, score} для подмешивания в контекст (итерация 8.2).
    """
    if not base_url or not user_id:
        return []
    collection_name = get_qdrant_collection(
        redis_url, "CONVERSATION_MEMORY_COLLECTION", collection
    )
    must = [{"key": "user_id", "match": {"value": user_id}}]
    if chat_id:
        must.append({"key": "chat_id", "match": {"value": chat_id}})
    filter_conditions = {"must": must}
    if not query or not query.strip():
        query = "conversation"
    return search_qdrant(
        base_url,
        collection_name,
        query,
        top_k=top_k,
        embed_fn=embed_fn,
        filter_conditions=filter_conditions,
    )


def delete_points_by_filter(
    base_url: str,
    collection: str,
    filter_conditions: dict[str, Any],
    client: httpx.Client | None = None,
) -> bool:
    """Удалить точки в коллекции по фильтру. POST /collections/{name}/points/delete с filter."""
    if not base_url or not collection or not filter_conditions:
        return False
    url = f"{base_url}/collections/{collection}/points/delete"
    payload = {"filter": filter_conditions}
    own = client is None
    if own:
        client = httpx.Client(timeout=15.0)
    try:
        r = client.post(url, json=payload)
        return 200 <= r.status_code < 300
    except Exception as e:
        logger.debug("delete_points_by_filter: %s", e)
        return False
    finally:
        if own and client:
            client.close()


def clear_conversation_memory(
    base_url: str,
    user_id: str,
    chat_id: str | None = None,
    collection: str = CONVERSATION_MEMORY_COLLECTION,
    redis_url: str | None = None,
) -> tuple[bool, str]:
    """
    Удалить из Qdrant все точки коллекции conversation_memory для user_id (и опционально chat_id).
    Возвращает (успех, сообщение об ошибке или ""). Итерация 8.3.
    """
    if not base_url or not user_id:
        return False, "Qdrant не настроен или не указан user_id"
    collection_name = get_qdrant_collection(
        redis_url, "CONVERSATION_MEMORY_COLLECTION", collection
    )
    must = [{"key": "user_id", "match": {"value": user_id}}]
    if chat_id:
        must.append({"key": "chat_id", "match": {"value": chat_id}})
    ok = delete_points_by_filter(base_url, collection_name, {"must": must})
    if not ok:
        return False, "Не удалось удалить точки в Qdrant"
    return True, ""
