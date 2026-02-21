"""Tests for event payloads."""

from assistant.core.events import IncomingMessage, OutgoingReply


def test_incoming_message_roundtrip():
    payload = IncomingMessage(
        message_id="123",
        user_id="456",
        chat_id="789",
        text="hello",
        reasoning_requested=True,
    )
    raw = payload.model_dump_json()
    back = IncomingMessage.model_validate_json(raw)
    assert back.user_id == "456"
    assert back.reasoning_requested is True


def test_outgoing_reply():
    payload = OutgoingReply(task_id="t1", chat_id="c1", text="Hi", done=True)
    assert payload.done is True
