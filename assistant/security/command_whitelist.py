"""Command whitelist for shell skill. Reject dangerous commands."""

from __future__ import annotations

import re
import shlex
import logging
from typing import Sequence

logger = logging.getLogger(__name__)

# Patterns that are always forbidden
FORBIDDEN_PATTERNS = [
    re.compile(r"rm\s+(-rf?|--recursive)\s+/($|\s)", re.IGNORECASE),
    re.compile(r"rm\s+-rf?\s+/", re.IGNORECASE),
    re.compile(r"curl\s+(-sS)?\s*https?://", re.IGNORECASE),
    re.compile(r"wget\s+", re.IGNORECASE),
    re.compile(r"\bexec\s+", re.IGNORECASE),
    re.compile(r"^\s*>\s*/", re.MULTILINE),
    re.compile(r"\|\s*sh\s*$", re.IGNORECASE),
]


class CommandWhitelist:
    """Allow only whitelisted commands and reject forbidden patterns."""

    def __init__(self, allowed_commands: Sequence[str]) -> None:
        self._allowed = set(c.strip().lower() for c in allowed_commands if c.strip())

    def is_allowed(self, raw_command: str) -> tuple[bool, str]:
        """
        Return (allowed, reason).
        If allowed, reason is empty. If denied, reason explains why.
        """
        if not raw_command or not raw_command.strip():
            return False, "empty command"
        parts = shlex.split(raw_command)
        if not parts:
            return False, "empty command"
        cmd = parts[0].lower()
        if cmd not in self._allowed:
            return False, f"command not in whitelist: {cmd}"
        for pat in FORBIDDEN_PATTERNS:
            if pat.search(raw_command):
                return False, f"forbidden pattern: {pat.pattern[:50]}"
        return True, ""

    def parse_command(self, raw_command: str) -> tuple[list[str], str] | None:
        """
        Parse into [cmd, ...args]. Returns None if not allowed.
        Second value is error message if not allowed.
        """
        ok, reason = self.is_allowed(raw_command)
        if not ok:
            return None
        try:
            return (shlex.split(raw_command), "")
        except ValueError as e:
            return None
