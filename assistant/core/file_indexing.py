"""Индексация вложений: извлечение текста → чанки → векторная память; ссылки на файлы в Redis (без хранения самих файлов)."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

FILE_REF_PREFIX = "file_ref:"
FILE_REF_USER_PREFIX = "file_ref_user:"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


def _extract_text(path: Path, mime_type: str, filename: str) -> str:
    """Извлечь текст из файла. Поддержка: txt, pdf, docx. Остальное — пустая строка."""
    suffix = (filename or path.name).lower()
    if suffix.endswith(".txt") or "text/plain" in (mime_type or ""):
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.warning("Read text file %s: %s", path, e)
            return ""
    if suffix.endswith(".pdf") or "pdf" in (mime_type or ""):
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            return "\n".join(p.extract_text() or "" for p in reader.pages)
        except ImportError:
            logger.debug("pypdf not installed, skip PDF extraction")
            return ""
        except Exception as e:
            logger.warning("PDF extraction %s: %s", path, e)
            return ""
    if suffix.endswith(".docx") or "wordprocessingml" in (mime_type or ""):
        try:
            from docx import Document

            doc = Document(str(path))
            return "\n".join(p.text for p in doc.paragraphs)
        except ImportError:
            logger.debug("python-docx not installed, skip DOCX extraction")
            return ""
        except Exception as e:
            logger.warning("DOCX extraction %s: %s", path, e)
            return ""
    if "image/" in (mime_type or ""):
        return " [изображение] "
    return ""


def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Разбить текст на чанки с перекрытием."""
    if not text or not text.strip():
        return []
    chunks = []
    start = 0
    text = text.strip()
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk.strip())
        start = end - overlap if overlap < chunk_size else end
    return chunks


def _save_file_ref_sync(redis_url: str, ref_id: str, user_id: str, data: dict[str, Any]) -> None:
    import redis

    client = redis.from_url(redis_url, decode_responses=True)
    client.set(FILE_REF_PREFIX + ref_id, json.dumps(data, ensure_ascii=False))
    client.sadd(FILE_REF_USER_PREFIX + user_id, ref_id)


def _get_file_ref_sync(redis_url: str, ref_id: str) -> dict[str, Any] | None:
    import redis

    client = redis.from_url(redis_url, decode_responses=True)
    raw = client.get(FILE_REF_PREFIX + ref_id)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _list_file_refs_sync(redis_url: str, user_id: str) -> list[str]:
    import redis

    client = redis.from_url(redis_url, decode_responses=True)
    refs = client.smembers(FILE_REF_USER_PREFIX + user_id)
    return list(refs) if refs else []


# Максимум символов извлечённого текста для передачи в оркестратор (summary)
EXTRACTED_TEXT_CAP = 6000


async def index_telegram_attachments(
    redis_url: str,
    memory: Any,
    user_id: str,
    chat_id: str,
    attachments: list[dict[str, Any]],
    bot_token: str,
) -> tuple[list[str], str]:
    """
    Скачать вложения из Telegram, извлечь текст, положить чанки в векторную память,
    сохранить ссылки на файлы в Redis (file_id для последующей отправки по запросу).
    Возвращает (список file_ref_id, извлечённый текст до EXTRACTED_TEXT_CAP символов для summary).
    """
    if not bot_token or not attachments:
        return [], ""
    base_url = f"https://api.telegram.org/bot{bot_token}"
    ref_ids: list[str] = []
    extracted_parts: list[str] = []
    for att in attachments:
        if att.get("source") != "telegram":
            continue
        file_id = att.get("file_id")
        filename = att.get("filename") or "file"
        mime_type = att.get("mime_type") or ""
        if not file_id:
            continue
        ref_id = str(uuid.uuid4())[:12]
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(f"{base_url}/getFile", params={"file_id": file_id}, timeout=10.0)
            data = r.json()
            if not data.get("ok"):
                logger.warning("Telegram getFile failed: %s", data)
                continue
            file_path = data.get("result", {}).get("file_path")
            if not file_path:
                continue
            download_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(filename).suffix) as tmp:
                tmp_path = Path(tmp.name)
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(download_url, timeout=30.0)
                resp.raise_for_status()
                tmp_path.write_bytes(resp.content)
                text = _extract_text(tmp_path, mime_type, filename)
                if text.strip():
                    extracted_parts.append(f"[{filename}]\n{text}")
                chunks = _chunk_text(text)
                for i, chunk in enumerate(chunks):
                    await memory.add_to_vector(
                        user_id,
                        chunk,
                        metadata={
                            "source": "file",
                            "file_ref_id": ref_id,
                            "filename": filename,
                            "chunk_index": i,
                        },
                    )
                _save_file_ref_sync(
                    redis_url,
                    ref_id,
                    user_id,
                    {
                        "file_id": file_id,
                        "chat_id": chat_id,
                        "user_id": user_id,
                        "filename": filename,
                        "source": "telegram",
                    },
                )
                ref_ids.append(ref_id)
            finally:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
        except Exception as e:
            logger.exception("Index attachment %s: %s", filename, e)
    combined = "\n\n".join(extracted_parts)
    if len(combined) > EXTRACTED_TEXT_CAP:
        combined = combined[: EXTRACTED_TEXT_CAP] + "\n\n[...]"
    return ref_ids, combined


def get_file_ref(redis_url: str, ref_id: str) -> dict[str, Any] | None:
    """Получить ссылку на файл по ref_id (для отправки в чат по file_id)."""
    return _get_file_ref_sync(redis_url, ref_id)


def list_file_refs(redis_url: str, user_id: str) -> list[dict[str, Any]]:
    """Список сохранённых ссылок на файлы пользователя (filename, ref_id)."""
    ref_ids = _list_file_refs_sync(redis_url, user_id)
    result = []
    for rid in ref_ids:
        ref = _get_file_ref_sync(redis_url, rid)
        if ref:
            result.append({"file_ref_id": rid, "filename": ref.get("filename") or rid})
    return result
