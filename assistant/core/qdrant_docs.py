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
