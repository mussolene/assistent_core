"""Streaming contract for model responses.

All model gateways (local OpenAI-compatible, LM Studio native, cloud) produce
an iteration of text tokens. The channel layer consumes this as a single
stream of strings (content deltas).

- OpenAI Chat Completions stream: delta.content per chunk.
- LM Studio native SSE: message.delta (reasoning.delta is optional and may be hidden).
- Outbound contract: AsyncIterator[str] or sync iterator; gateway.generate(stream=True)
  returns either an async generator yielding tokens or a coroutine returning full text.

Streaming is documented in README and uses assistant.core.events.StreamToken
on the bus for channel adapters (e.g. Telegram editMessageText).
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable


@runtime_checkable
class StreamingProtocol(Protocol):
    """Protocol for providers that support token streaming."""

    async def stream_tokens(self, prompt: str, **kwargs: object) -> AsyncIterator[str]:
        """Yield content tokens. Caller may pass system, reasoning, etc."""
        ...
