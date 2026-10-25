import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.contracts import NormalizedInbound
from app.core import timeutil


@dataclass(frozen=True)
class ConversationRef:
    customer_id: uuid.UUID
    conversation_id: uuid.UUID
    created: bool


def usage_period() -> date:
    moment = timeutil.to_ist(timeutil.utc_now())
    return moment.date().replace(day=1)


async def get_or_create_customer(session: AsyncSession, inbound: NormalizedInbound) -> uuid.UUID:
    if inbound.channel == "whatsapp":
        column, value = "phone_e164", inbound.from_phone
    else:
        column, value = "email_lower", (inbound.from_email or "").strip().lower()
    if not value:
        raise ValueError("inbound has no sender")
    if column == "phone_e164":
        insert = text(
            "INSERT INTO customers (phone_e164) VALUES (:v)"
            " ON CONFLICT (tenant_id, phone_e164) WHERE phone_e164 IS NOT NULL DO NOTHING"
        )
        select = text("SELECT id FROM customers WHERE phone_e164 = :v")
    else:
        insert = text(
            "INSERT INTO customers (email_lower) VALUES (:v)"
            " ON CONFLICT (tenant_id, email_lower) WHERE email_lower IS NOT NULL DO NOTHING"
        )
        select = text("SELECT id FROM customers WHERE email_lower = :v")
    await session.execute(insert, {"v": value})
    return uuid.UUID(str((await session.execute(select, {"v": value})).scalar_one()))


async def get_or_create_conversation(
    session: AsyncSession, customer_id: uuid.UUID, channel: str
) -> ConversationRef:
    existing = (
        await session.execute(
            text(
                "SELECT id FROM conversations WHERE customer_id = :c AND channel = :ch"
                " AND status IN ('open','handoff') ORDER BY created_at DESC LIMIT 1"
            ),
            {"c": customer_id, "ch": channel},
        )
    ).first()
    if existing is not None:
        return ConversationRef(customer_id, existing.id, False)
    created = (
        await session.execute(
            text(
                "INSERT INTO conversations (customer_id, channel, counted_for_usage)"
                " VALUES (:c, :ch, true) ON CONFLICT (tenant_id, customer_id, channel)"
                " WHERE status IN ('open','handoff') DO NOTHING RETURNING id"
            ),
            {"c": customer_id, "ch": channel},
        )
    ).first()
    if created is None:
        row = (
            await session.execute(
                text(
                    "SELECT id FROM conversations WHERE customer_id = :c AND channel = :ch"
                    " AND status IN ('open','handoff') ORDER BY created_at DESC LIMIT 1"
                ),
                {"c": customer_id, "ch": channel},
            )
        ).one()
        return ConversationRef(customer_id, row.id, False)
    await session.execute(
        text(
            "INSERT INTO usage_counters (period_start, conversation_count) VALUES (:p, 1)"
            " ON CONFLICT (tenant_id, period_start) DO UPDATE SET"
            " conversation_count = usage_counters.conversation_count + 1"
        ),
        {"p": usage_period()},
    )
    return ConversationRef(customer_id, created.id, True)
