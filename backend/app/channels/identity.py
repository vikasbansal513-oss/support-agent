import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.contracts import E164_RE, NormalizedInbound
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


# ------------------------------------------------------------------ omnichannel identity link
# get_or_create_customer() above only ever looks up ONE identifier - the one the inbound
# channel carries (phone for WhatsApp/voice, email for Gmail). So the same real person
# messaging on two channels gets two separate `customers` rows unless something else tells
# us they're the same person. link_customer_identities() is that something else: called
# (best-effort, from the runners) whenever a Shopify order lookup succeeds and the order
# itself carries both a phone and an email - a trusted source, not customer-asserted - it
# folds the per-channel rows into one so conversations/handoffs/limits/dedup all share one
# customer_id across every channel that customer has used.
async def link_customer_identities(
    session: AsyncSession, phone_e164: str | None, email_lower: str | None
) -> None:
    if not phone_e164 or not email_lower or not E164_RE.match(phone_e164):
        return
    email_lower = email_lower.strip().lower()
    if not email_lower:
        return
    phone_row = (
        await session.execute(
            text("SELECT id, email_lower FROM customers WHERE phone_e164 = :p"),
            {"p": phone_e164},
        )
    ).first()
    email_row = (
        await session.execute(
            text("SELECT id, phone_e164 FROM customers WHERE email_lower = :e"),
            {"e": email_lower},
        )
    ).first()
    if phone_row is None and email_row is None:
        return  # neither channel has ever talked to us under its own identifier yet
    if phone_row is not None and email_row is None:
        if phone_row.email_lower is None:
            await session.execute(
                text("UPDATE customers SET email_lower = :e WHERE id = :c"),
                {"e": email_lower, "c": phone_row.id},
            )
        return
    if email_row is not None and phone_row is None:
        if email_row.phone_e164 is None:
            await session.execute(
                text("UPDATE customers SET phone_e164 = :p WHERE id = :c"),
                {"p": phone_e164, "c": email_row.id},
            )
        return
    if phone_row.id == email_row.id:
        return  # already linked
    await _merge_customers(session, keep=phone_row.id, drop=email_row.id)


async def _merge_customers(session: AsyncSession, *, keep: uuid.UUID, drop: uuid.UUID) -> None:
    """Two pre-existing `customers` rows turned out to be the same person. Repoints every
    child row from `drop` to `keep`, then removes `drop`. By construction a channel-origin
    customer row only ever has conversations/opt-outs on the channel it was first seen on
    (get_or_create_customer looks up by a single identifier), so `keep` and `drop` cannot
    already hold an open conversation or opt-out on the SAME channel - there is nothing here
    to reconcile a clash for. action_usage is the one exception: it's a per-day/per-tool
    SPEND counter (Decision Framework's refund/limit tracking), so an overlapping row is
    summed into `keep`, not overwritten - that is the mathematically correct combined total
    for one real customer, not a side effect to avoid."""
    await session.execute(
        text(
            "UPDATE customers AS k SET"
            " phone_e164 = COALESCE(k.phone_e164, d.phone_e164),"
            " email_lower = COALESCE(k.email_lower, d.email_lower)"
            " FROM customers AS d WHERE k.id = :keep AND d.id = :drop"
        ),
        {"keep": keep, "drop": drop},
    )
    await session.execute(
        text("UPDATE conversations SET customer_id = :keep WHERE customer_id = :drop"),
        {"keep": keep, "drop": drop},
    )
    await session.execute(
        text("UPDATE voice_calls SET customer_id = :keep WHERE customer_id = :drop"),
        {"keep": keep, "drop": drop},
    )
    await session.execute(
        text(
            "INSERT INTO action_usage (customer_id, day, tool, count, sum_paise)"
            " SELECT :keep, day, tool, count, sum_paise FROM action_usage WHERE customer_id = :drop"
            " ON CONFLICT (tenant_id, customer_id, day, tool) DO UPDATE SET"
            " count = action_usage.count + EXCLUDED.count,"
            " sum_paise = action_usage.sum_paise + EXCLUDED.sum_paise"
        ),
        {"keep": keep, "drop": drop},
    )
    await session.execute(
        text("DELETE FROM action_usage WHERE customer_id = :drop"), {"drop": drop}
    )
    await session.execute(
        text(
            "INSERT INTO opt_outs (customer_id, channel, source, created_at)"
            " SELECT :keep, channel, source, created_at FROM opt_outs WHERE customer_id = :drop"
            " ON CONFLICT (tenant_id, customer_id, channel) DO NOTHING"
        ),
        {"keep": keep, "drop": drop},
    )
    await session.execute(text("DELETE FROM opt_outs WHERE customer_id = :drop"), {"drop": drop})
    await session.execute(text("DELETE FROM customers WHERE id = :drop"), {"drop": drop})
