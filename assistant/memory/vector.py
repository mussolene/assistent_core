"""Vector memory: local embeddings and similarity search. Уровни: кратковременная, среднесрочная, долговременная."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class VectorMemory:
    """In-process vector store using sentence-transformers. Поддержка max_size (FIFO) и clear()."""

    def __init__(
        self,
        collection: str = "assistant_memory",
        top_k: int = 5,
        persist_path: str | Path | None = None,
        max_size: int | None = None,
    ) -> None:
        self._collection = collection
        self._top_k = top_k
        self._persist_path = (
            Path(persist_path) if persist_path else Path("/tmp/assistant_vectors.json")
        )
        self._max_size = max_size
        self._model = None
        self._documents: list[dict[str, Any]] = []
        self._vectors: list[list[float]] = []
        self._loaded = False

    def _get_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer("all-MiniLM-L6-v2")
            except Exception as e:
                logger.warning(
                    "sentence_transformers not available: %s. Vector memory disabled.", e
                )
        return self._model

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self._persist_path.exists():
            try:
                data = json.loads(self._persist_path.read_text(encoding="utf-8"))
                self._documents = data.get("documents", [])
                self._vectors = data.get("vectors", [])
            except Exception as e:
                logger.warning("Could not load vector store: %s", e)

    def _save(self) -> None:
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        self._persist_path.write_text(
            json.dumps({"documents": self._documents, "vectors": self._vectors}),
            encoding="utf-8",
        )

    def add(self, text: str, metadata: dict[str, Any] | None = None) -> None:
        model = self._get_model()
        if model is None:
            return
        self._load()
        vec = model.encode(text).tolist()
        doc = {
            "text": text,
            "metadata": metadata or {},
            "id": hashlib.sha256(text.encode()).hexdigest()[:16],
        }
        self._documents.append(doc)
        self._vectors.append(vec)
        if self._max_size is not None and len(self._documents) > self._max_size:
            self._documents = self._documents[-self._max_size :]
            self._vectors = self._vectors[-self._max_size :]
        self._save()

    def clear(self) -> None:
        """Очистить хранилище векторов (документы и векторы)."""
        self._load()
        self._documents = []
        self._vectors = []
        self._save()
        logger.info("Vector memory cleared: %s", self._persist_path)

    def search(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        model = self._get_model()
        if model is None or not self._vectors:
            return []
        self._load()
        k = top_k or self._top_k
        qvec = model.encode(query).tolist()
        scores = []
        for i, v in enumerate(self._vectors):
            sim = self._cosine(qvec, v)
            scores.append((i, sim))
        scores.sort(key=lambda x: -x[1])
        return [{**self._documents[idx], "score": score} for idx, score in scores[:k]]

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)
