import logging
from typing import Protocol

import httpx

from app.core.config import get_settings

logger = logging.getLogger("app.mail")
BREVO_URL = "https://api.brevo.com/v3/smtp/email"


class Mailer(Protocol):
    async def send(self, to: str, subject: str, text: str) -> None: ...


class BrevoMailer:
    async def send(self, to: str, subject: str, text: str) -> None:
        settings = get_settings()
        if not settings.brevo_api_key or not settings.mail_from:
            raise RuntimeError("mail is not configured")
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                BREVO_URL,
                headers={"api-key": settings.brevo_api_key, "accept": "application/json"},
                json={
                    "sender": {"email": settings.mail_from},
                    "to": [{"email": to}],
                    "subject": subject,
                    "textContent": text,
                },
            )
        if response.status_code >= 300:
            raise RuntimeError(f"mail provider returned {response.status_code}")


def get_mailer() -> Mailer:
    return BrevoMailer()


async def send_safely(mailer: Mailer, to: str, subject: str, text: str) -> bool:
    try:
        await mailer.send(to, subject, text)
    except Exception as exc:
        logger.error("mail send failed", extra={"error_class": type(exc).__name__})
        return False
    return True
