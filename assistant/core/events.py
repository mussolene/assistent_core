"""Event payloads for the Event Bus. All events are Pydantic models."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ChannelKind(str, Enum):
    TELEGRAM = "telegram"


class IncomingMessage(BaseModel):
    """Published when a user sends a message (e.g. from Telegram)."""

    message_id: str = Field(description="External message id")
    user_id: str = Field(description="User id in the channel")
    chat_id: str = Field(description="Chat/conversation id")
    channel: ChannelKind = ChannelKind.TELEGRAM
    text: str = Field(default="", description="Message text")
    reasoning_requested: bool = Field(default=False)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskCreated(BaseModel):
    """Internal: task was created by Orchestrator."""

    task_id: str
    user_id: str
    chat_id: str
    channel: ChannelKind = ChannelKind.TELEGRAM
    message_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentResult(BaseModel):
    """Result from an agent (assistant or tool)."""

    task_id: str
    agent_type: str = Field(description="assistant | tool")
    success: bool = True
    output_text: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    next_agent: Optional[str] = None
    error: Optional[str] = None
    stream_id: Optional[str] = Field(default=None, description="For streaming replies")


class OutgoingReply(BaseModel):
    """Send a reply back to the channel (e.g. Telegram)."""

    task_id: str
    chat_id: str
    message_id: str = Field(default="", description="Original message id for threading")
    text: str = ""
    done: bool = Field(default=True, description="True when reply is complete (streaming)")
    reasoning_requested: bool = False


class StreamToken(BaseModel):
    """Single token for streaming reply."""

    task_id: str
    chat_id: str
    token: str
    done: bool = False
