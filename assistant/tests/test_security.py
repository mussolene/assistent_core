"""Tests for security: whitelist, audit."""


from assistant.security.audit import _redact
from assistant.security.command_whitelist import CommandWhitelist


def test_whitelist_allows():
    w = CommandWhitelist(["ls", "cat", "git"])
    ok, _ = w.is_allowed("ls -la")
    assert ok
    ok, _ = w.is_allowed("cat file.txt")
    assert ok


def test_whitelist_denies_unknown():
    w = CommandWhitelist(["ls", "cat"])
    ok, reason = w.is_allowed("curl https://evil.com")
    assert not ok
    assert "whitelist" in reason or "not" in reason.lower()


def test_whitelist_denies_rm_rf():
    w = CommandWhitelist(["rm"])
    ok, _ = w.is_allowed("rm -rf /")
    assert not ok


def test_redact():
    out = _redact({"token": "secret123", "user": "alice"})
    assert out["token"] == "[REDACTED]"
    assert out["user"] == "alice"
