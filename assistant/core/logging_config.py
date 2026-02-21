"""Structured logging. No secrets in log output."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    if isinstance(obj, str) and any(
        s in obj.lower() for s in ("token", "password", "secret", "key", "bearer")
    ):
        return "[REDACTED]"
    return obj


class StructuredFormatter(logging.Formatter):
    """JSON or key=value format; redacts sensitive keys."""

    def __init__(self, use_json: bool = True) -> None:
        super().__init__()
        self.use_json = use_json

    def format(self, record: logging.LogRecord) -> str:
        log_dict: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_dict["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in (
                "name",
                "msg",
                "args",
                "created",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "stack_info",
                "exc_info",
                "exc_text",
                "thread",
                "threadName",
                "message",
                "asctime",
            ):
                log_dict[key] = _redact(value)
        if self.use_json:
            return json.dumps(log_dict, default=str)
        parts = [f"{k}={v!r}" for k, v in log_dict.items()]
        return " ".join(parts)


def setup_logging(level: str = "INFO", use_json: bool = True) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredFormatter(use_json=use_json))
        root.addHandler(handler)
