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


def test_incoming_message_attachments():
    payload = IncomingMessage(
        message_id="1",
        user_id="u1",
        chat_id="c1",
        text="",
        attachments=[{"file_id": "abc", "filename": "doc.pdf", "source": "telegram"}],
    )
    assert len(payload.attachments) == 1
    assert payload.attachments[0]["file_id"] == "abc"


def test_outgoing_reply_send_document():
    payload = OutgoingReply(
        task_id="t1",
        chat_id="c1",
        text="Файл во вложении.",
        done=True,
        send_document={"file_id": "AgACAgIAAxkB"},
    )
    assert payload.send_document is not None
    assert payload.send_document["file_id"] == "AgACAgIAAxkB"
