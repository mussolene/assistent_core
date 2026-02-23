"""Тесты воркера перезапуска по Redis-флагу (ROADMAP F)."""

import json
from unittest.mock import MagicMock, patch

# Скрипт в scripts/ доступен при pythonpath=["."]
from scripts.restart_worker import RESTART_REQUESTED_KEY, run_one_poll


def test_run_one_poll_no_key_returns_false():
    """Если ключа нет в Redis, run_one_poll возвращает False и не вызывает subprocess."""
    client = MagicMock()
    client.get.return_value = None
    with patch("redis.from_url", return_value=client):
        with patch("scripts.restart_worker.subprocess.run") as mock_run:
            result = run_one_poll("redis://localhost:6379/0", "echo ok")
    assert result is False
    client.get.assert_called_once_with(RESTART_REQUESTED_KEY)
    client.delete.assert_not_called()
    mock_run.assert_not_called()


def test_run_one_poll_deletes_key_and_runs_cmd():
    """При наличии ключа воркер удаляет его и выполняет RESTART_CMD."""
    payload = {"user_id": 12345, "timestamp": 1000.0}
    client = MagicMock()
    client.get.return_value = json.dumps(payload)
    with patch("redis.from_url", return_value=client):
        with patch("scripts.restart_worker.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
            result = run_one_poll("redis://localhost:6379/0", "echo done")
    assert result is True
    client.get.assert_called_once_with(RESTART_REQUESTED_KEY)
    client.delete.assert_called_once_with(RESTART_REQUESTED_KEY)
    mock_run.assert_called_once()
    assert mock_run.call_args[0][0] == "echo done"


def test_run_one_poll_dry_run_when_cmd_empty():
    """При пустом RESTART_CMD воркер только логирует и удаляет ключ (dry run)."""
    client = MagicMock()
    client.get.return_value = json.dumps({"user_id": 1, "timestamp": 0})
    with patch("redis.from_url", return_value=client):
        with patch("scripts.restart_worker.subprocess.run") as mock_run:
            result = run_one_poll("redis://localhost:6379/0", "")
    assert result is True
    client.delete.assert_called_once_with(RESTART_REQUESTED_KEY)
    mock_run.assert_not_called()
