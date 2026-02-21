"""Structured audit log. No secrets or PII in output."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("assistant.audit")

# Redact keys that may contain secrets
REDACT_KEYS = frozenset({"token", "password", "secret", "api_key", "authorization", "cookie"})


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: "[REDACTED]" if (isinstance(k, str) and k.lower() in REDACT_KEYS) else _redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def audit(event: str, **kwargs: Any) -> None:
    """Log a structured audit event. Keys like token/password are redacted."""
    payload = _redact(dict(kwargs))
    payload["event"] = event
    payload["timestamp"] = datetime.now(timezone.utc).isoformat()
    logger.info("audit: %s", payload)
