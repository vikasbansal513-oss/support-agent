import uuid
from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import session_scope
from app.integrations import service as integrations
from app.integrations.gmail import EmailSender, HttpGmailClient
from app.integrations.gupshup import GupshupSender, HttpGupshupSender
from app.integrations.http import DbCallRecorder, ExternalHttp
from app.voice.sender import VoiceSender

Factory = async_sessionmaker[AsyncSession]


@dataclass
class Senders:
    whatsapp: GupshupSender | None = None
    email: EmailSender | None = None
    email_address: str | None = None
    voice: VoiceSender | None = None


_override: Senders | None = None


def set_override(senders: Senders | None) -> None:
    global _override
    _override = senders


async def build_senders(
    factory: Factory, tenant_id: uuid.UUID, transport: httpx.AsyncBaseTransport | None = None
) -> Senders:
    if _override is not None:
        return _override
    async with session_scope(factory, tenant_id=tenant_id) as session:
        gupshup = await integrations.load_secret(session, "gupshup")
        gmail = await integrations.load_secret(session, "gmail")
    senders = Senders()
    if gupshup is not None:
        http = ExternalHttp(
            "gupshup", recorder=DbCallRecorder(factory, tenant_id), transport=transport
        )
        senders.whatsapp = HttpGupshupSender(http, gupshup[1]["userid"], gupshup[1]["password"])
    if gmail is not None:
        http = ExternalHttp(
            "gmail", recorder=DbCallRecorder(factory, tenant_id), transport=transport
        )
        senders.email = HttpGmailClient(http, gmail[1]["refresh_token"])
        senders.email_address = gmail[0].get("address")
    return senders
