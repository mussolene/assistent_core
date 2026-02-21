"""Tests for core/notify: dev chat id, notify_main_channel, pending confirmation, dev feedback."""

from unittest.mock import MagicMock, patch

import pytest

from assistant.core import notify


def test_get_dev_chat_id_from_env():
    with patch("assistant.dashboard.config_store.get_config_from_redis_sync", return_value={}):
        with patch.dict("os.environ", {"TELEGRAM_DEV_CHAT_ID": "999"}):
            assert notify.get_dev_chat_id() == "999"


def test_get_dev_chat_id_from_redis():
    with patch("assistant.dashboard.config_store.get_config_from_redis_sync", return_value={"TELEGRAM_DEV_CHAT_ID": "111"}):
        assert notify.get_dev_chat_id() == "111"


def test_get_dev_chat_id_fallback_to_allowed():
    with patch("assistant.dashboard.config_store.get_config_from_redis_sync", return_value={"TELEGRAM_ALLOWED_USER_IDS": [123, 456]}):
        assert notify.get_dev_chat_id() == "123"


def test_notify_main_channel_no_chat_id():
    with patch("assistant.core.notify.get_dev_chat_id", return_value=None):
        assert notify.notify_main_channel("hi") is False


def test_notify_main_channel_success():
    mock_r = MagicMock()
    mock_r.ping = MagicMock()
    mock_r.publish = MagicMock(return_value=1)
    mock_r.close = MagicMock()
    with patch("assistant.core.notify.get_dev_chat_id", return_value="123"):
        with patch("redis.from_url", return_value=mock_r):
            with patch("assistant.core.notify._get_redis_url", return_value="redis://localhost/0"):
                assert notify.notify_main_channel("test") is True
    mock_r.publish.assert_called_once()


def test_consume_pending_confirmation_no_pending():
    r = MagicMock()
    r.get.return_value = None
    r.close = MagicMock()
    with patch("redis.from_url", return_value=r):
        assert notify.consume_pending_confirmation("123", "hello") is False


def test_consume_pending_confirmation_confirm():
    import json
    r = MagicMock()
    r.get.return_value = json.dumps({"message": "Deploy?", "created_at": 0, "result": None})
    r.close = MagicMock()
    with patch("redis.from_url", return_value=r):
        with patch("assistant.core.notify.set_pending_confirmation_result") as set_result:
            out = notify.consume_pending_confirmation("123", "confirm")
            assert out is True
            set_result.assert_called_once()
            arg = set_result.call_args[0][1]
            assert arg["confirmed"] is True
            assert arg["reply"] == "confirm"


def test_push_and_pop_dev_feedback():
    r = MagicMock()
    r.rpush = MagicMock()
    r.expire = MagicMock()
    r.close = MagicMock()
    r.lrange = MagicMock(return_value=["msg1", "msg2"])
    r.delete = MagicMock()
    with patch("redis.from_url", return_value=r):
        with patch("assistant.core.notify._get_redis_url", return_value="redis://localhost/0"):
            with patch("assistant.dashboard.mcp_endpoints.get_endpoint_id_for_chat", return_value=None):
                notify.push_dev_feedback("123", "hello")
            assert r.rpush.call_count >= 1
            items = notify.pop_dev_feedback("123")
            assert items == ["msg1", "msg2"]
            r.delete.assert_called_once()
