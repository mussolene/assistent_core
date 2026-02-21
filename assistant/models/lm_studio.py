"""LM Studio native API: stream only message.delta (reasoning hidden). See https://lmstudio.ai/docs/developer/rest."""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import httpx

logger = logging.getLogger(__name__)


def _native_base_url(openai_base_url: str) -> str:
    """Convert OpenAI-compat base (e.g. http://localhost:1234/v1) to LM Studio native root."""
    u = (openai_base_url or "").rstrip("/")
    if u.endswith("/v1"):
        u = u[:-3]
    return u.rstrip("/") or "http://localhost:1234"


async def generate_lm_studio(
    base_url: str,
    model: str,
    prompt: str,
    *,
    system: str | None = None,
    api_key: str = "",
    reasoning: str = "on",
) -> str:
    """Non-streaming: POST /api/v1/chat, return concatenated message content (reasoning excluded)."""
    root = _native_base_url(base_url)
    url = f"{root}/api/v1/chat"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = {
        "model": model,
        "input": prompt,
        "stream": False,
        "reasoning": reasoning,
    }
    if system:
        body["system_prompt"] = system
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(url, json=body, headers=headers)
        r.raise_for_status()
        data = r.json()
    out_parts = []
    for item in data.get("output") or []:
        if isinstance(item, dict) and item.get("type") == "message":
            out_parts.append(item.get("content") or "")
    return "".join(out_parts).strip()


def stream_lm_studio(
    base_url: str,
    model: str,
    prompt: str,
    *,
    system: str | None = None,
    api_key: str = "",
    reasoning: str = "on",
) -> AsyncIterator[str]:
    """Stream via SSE: only yield message.delta content (reasoning.delta ignored)."""

    async def _stream() -> AsyncIterator[str]:
        root = _native_base_url(base_url)
        url = f"{root}/api/v1/chat"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        body = {
            "model": model,
            "input": prompt,
            "stream": True,
            "reasoning": reasoning,
        }
        if system:
            body["system_prompt"] = system
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", url, json=body, headers=headers) as resp:
                resp.raise_for_status()
                event_type: str | None = None
                buf = b""
                async for chunk in resp.aiter_bytes():
                    buf += chunk
                    while b"\n\n" in buf:
                        part, buf = buf.split(b"\n\n", 1)
                        part = part.strip()
                        if not part:
                            continue
                        for line in part.split(b"\n"):
                            line = line.strip()
                            if line.startswith(b"event:"):
                                event_type = line[6:].strip().decode("utf-8", errors="replace")
                            elif line.startswith(b"data:") and event_type:
                                try:
                                    raw = line[5:].strip().decode("utf-8", errors="replace")
                                    data = json.loads(raw)
                                    if event_type == "message.delta":
                                        content = data.get("content") or ""
                                        if content:
                                            yield content
                                    elif event_type == "error":
                                        msg = (data.get("error") or {}).get("message", "")
                                        if msg:
                                            logger.warning("LM Studio stream error: %s", msg)
                                except json.JSONDecodeError:
                                    pass
                                event_type = None

    return _stream()


def is_lm_studio_native_url(base_url: str) -> bool:
    """Heuristic: default LM Studio port or path contains api/v1."""
    if not base_url:
        return False
    return "1234" in base_url or "/api/v1" in base_url
