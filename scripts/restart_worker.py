#!/usr/bin/env python3
"""
Воркер перезапуска по флагу в Redis (ROADMAP F).

Следит за ключом assistant:action:restart_requested. При появлении значения
выполняет команду из RESTART_CMD (например docker compose restart) и удаляет ключ.

Переменные окружения:
  REDIS_URL — подключение к Redis (по умолчанию redis://localhost:6379/0)
  RESTART_CMD — команда для перезапуска (по умолчанию "docker compose restart")
  RESTART_POLL_SEC — интервал опроса в секундах (по умолчанию 10)

Запуск:
  python scripts/restart_worker.py
  python scripts/restart_worker.py --once   # один проход (для тестов/cron)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time

RESTART_REQUESTED_KEY = "assistant:action:restart_requested"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def get_redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")


def run_one_poll(redis_url: str, restart_cmd: str) -> bool:
    """
    Один проход: проверить ключ, при наличии — выполнить команду и удалить ключ.
    Возвращает True, если был запрос на перезапуск и он обработан.
    """
    try:
        import redis

        client = redis.from_url(redis_url, decode_responses=True)
        raw = client.get(RESTART_REQUESTED_KEY)
        if not raw:
            client.close()
            return False
        try:
            payload = json.loads(raw)
            user_id = payload.get("user_id", "?")
            ts = payload.get("timestamp", 0)
            logger.info("Restart requested by user_id=%s at timestamp=%s", user_id, ts)
        except (TypeError, json.JSONDecodeError):
            logger.warning("Invalid payload in %s: %s", RESTART_REQUESTED_KEY, raw[:100])
        client.delete(RESTART_REQUESTED_KEY)
        client.close()
        if not restart_cmd:
            logger.info("RESTART_CMD not set; skipping execution (dry run).")
            return True
        logger.info("Executing: %s", restart_cmd)
        proc = subprocess.run(
            restart_cmd,
            shell=True,
            timeout=120,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            logger.error("Restart command failed (exit %s): %s", proc.returncode, proc.stderr)
        else:
            logger.info("Restart command completed successfully.")
        return True
    except subprocess.TimeoutExpired:
        logger.error("Restart command timed out after 120s")
        return True
    except Exception as e:
        logger.exception("Poll error: %s", e)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Restart worker: watch Redis and run restart command.")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one poll and exit (for cron or tests).",
    )
    args = parser.parse_args()
    redis_url = get_redis_url()
    restart_cmd = (os.getenv("RESTART_CMD") or "").strip() or "docker compose restart"
    poll_sec = int(os.getenv("RESTART_POLL_SEC", "10") or "10")
    if poll_sec < 1:
        poll_sec = 10

    if args.once:
        run_one_poll(redis_url, restart_cmd)
        return 0

    logger.info("Restart worker started (poll every %s s, cmd=%s)", poll_sec, restart_cmd)
    while True:
        try:
            run_one_poll(redis_url, restart_cmd)
        except Exception as e:
            logger.exception("Loop error: %s", e)
        time.sleep(poll_sec)


if __name__ == "__main__":
    sys.exit(main())
