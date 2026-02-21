"""Email channel: subscribe to OutgoingReply with channel=EMAIL, send via SMTP or SendGrid."""

from __future__ import annotations

import asyncio
import logging
import os
import smtplib
from email.mime.text import MIMEText
from typing import Any

import httpx

from assistant.core.bus import EventBus
from assistant.core.events import ChannelKind, OutgoingReply
from assistant.core.logging_config import setup_logging

logger = logging.getLogger(__name__)

DEFAULT_SUBJECT = "Assistant"
REDIS_URL_ENV = "REDIS_URL"


def _get_redis_url() -> str:
    return os.getenv(REDIS_URL_ENV, "redis://localhost:6379/0")


def get_email_config(redis_url: str) -> dict[str, Any]:
    """Load email settings from Redis. Returns dict with EMAIL_* keys."""
    try:
        from assistant.dashboard.config_store import get_config_from_redis_sync

        cfg = get_config_from_redis_sync(redis_url)
        return {
            "enabled": (cfg.get("EMAIL_ENABLED") or "").lower() in ("true", "1", "yes"),
            "from": (cfg.get("EMAIL_FROM") or "").strip(),
            "provider": (cfg.get("EMAIL_PROVIDER") or "smtp").strip().lower(),
            "smtp_host": (cfg.get("EMAIL_SMTP_HOST") or "").strip(),
            "smtp_port": (cfg.get("EMAIL_SMTP_PORT") or "587").strip(),
            "smtp_user": (cfg.get("EMAIL_SMTP_USER") or "").strip(),
            "smtp_password": (cfg.get("EMAIL_SMTP_PASSWORD") or "").strip(),
            "sendgrid_api_key": (cfg.get("EMAIL_SENDGRID_API_KEY") or "").strip(),
        }
    except Exception as e:
        logger.warning("get_email_config: %s", e)
        return {"enabled": False}


def _send_smtp(to: str, subject: str, body: str, config: dict[str, Any]) -> bool:
    """Send email via SMTP. Returns True on success."""
    host = config.get("smtp_host") or ""
    if not host:
        logger.warning("EMAIL_SMTP_HOST not set")
        return False
    port = int(config.get("smtp_port") or "587")
    user = config.get("smtp_user") or ""
    password = config.get("smtp_password") or ""
    from_addr = config.get("from") or user or "noreply@localhost"
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to
    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            if port == 587:
                smtp.starttls()
            if user and password:
                smtp.login(user, password)
            smtp.sendmail(from_addr, [to], msg.as_string())
        logger.info("Email sent via SMTP to %s", to)
        return True
    except Exception as e:
        logger.exception("SMTP send failed: %s", e)
        return False


def _send_sendgrid(to: str, subject: str, body: str, config: dict[str, Any]) -> bool:
    """Send email via SendGrid API. Returns True on success."""
    api_key = config.get("sendgrid_api_key") or ""
    if not api_key:
        logger.warning("EMAIL_SENDGRID_API_KEY not set")
        return False
    from_addr = config.get("from") or "noreply@localhost"
    payload = {
        "personalizations": [{"to": [{"email": to}]}],
        "from": {"email": from_addr, "name": "Assistant"},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
    }
    try:
        r = httpx.post(
            "https://api.sendgrid.com/v3/mail/send",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=15.0,
        )
        if r.status_code in (200, 202):
            logger.info("Email sent via SendGrid to %s", to)
            return True
        logger.warning("SendGrid failed %s: %s", r.status_code, r.text)
        return False
    except Exception as e:
        logger.exception("SendGrid send failed: %s", e)
        return False


def send_email(to: str, subject: str, body: str, redis_url: str) -> bool:
    """Send email using config from Redis (SMTP or SendGrid). to = recipient address."""
    config = get_email_config(redis_url)
    if not config.get("enabled"):
        logger.debug("Email disabled in config")
        return False
    if not to or "@" not in to:
        logger.warning("Invalid email recipient: %s", to)
        return False
    subject = subject or DEFAULT_SUBJECT
    body = body or ""
    if config.get("provider") == "sendgrid":
        return _send_sendgrid(to, subject, body, config)
    return _send_smtp(to, subject, body, config)


async def run_email_adapter() -> None:
    """Connect to bus, subscribe to OutgoingReply; on channel=EMAIL send email. chat_id = recipient."""
    setup_logging()
    redis_url = _get_redis_url()
    bus = EventBus(redis_url)
    await bus.connect()

    async def on_outgoing(payload: OutgoingReply) -> None:
        if payload.channel != ChannelKind.EMAIL:
            return
        to = (payload.chat_id or "").strip()
        if not to or "@" not in to:
            logger.warning("Email adapter: chat_id is not an email address: %s", to)
            return
        # Run blocking send in executor to avoid blocking the loop
        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(
            None,
            lambda: send_email(to, DEFAULT_SUBJECT, payload.text or "", redis_url),
        )
        if not ok:
            logger.warning("Email adapter: send failed for %s", to)

    bus.subscribe_outgoing(on_outgoing)
    logger.info("Email adapter subscribed to outgoing_reply (channel=email)")
    await bus.run_listener()

    await bus.disconnect()


def main() -> None:
    asyncio.run(run_email_adapter())


if __name__ == "__main__":
    main()
