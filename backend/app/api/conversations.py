import uuid
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.actions import engine
from app.actions.senders import build_senders
from app.api.handoffs import handoff_out
from app.api.pagination import decode_offset, encode_offset
from app.auth.deps import require_user
from app.auth.service import Principal
from app.contracts.contracts import (
    ConversationOut,
    CustomerOut,
    DecisionOut,
    HandoffOut,
    HandoffSummary,
    HumanReplyIn,
    MessageOut,
    Page,
    ReportIn,
)
from app.core.audit import AuditEvent, record
from app.core.db import get_session_factory, session_scope
from app.core.errors import AppError
from app.core.mail import Mailer, get_mailer
from app.core.masking import mask_email, mask_mobile
from app.handoffs import service as handoff_service
from app.integrations import service as integrations

router = APIRouter(tags=["conversations"])
Factory = async_sessionmaker[AsyncSession]
ConversationStatus = Literal["open", "handoff", "resolved", "closed"]

_COLUMNS = (
    "c.id, c.channel, c.status, c.active_order_ref, c.language, c.human_active, c.last_message_at,"
    " c.customer_id, cu.phone_e164, cu.email_lower"
)


class ConversationOutAny(ConversationOut):
    channel: Literal["whatsapp", "email", "voice"]  # type: ignore[assignment]


class ConversationDetailAny(ConversationOutAny):
    messages: list[MessageOut]


def conversation_out(row: Any) -> ConversationOutAny:
    return ConversationOutAny(
        id=row.id,
        channel=row.channel,
        status=row.status,
        customer=CustomerOut(
            id=row.customer_id,
            masked_phone=mask_mobile(row.phone_e164) if row.phone_e164 else None,
            masked_email=mask_email(row.email_lower) if row.email_lower else None,
        ),
        active_order_ref=row.active_order_ref,
        language=row.language,
        human_active=row.human_active,
        last_message_at=row.last_message_at,
    )


async def _conversation(session: AsyncSession, conversation_id: uuid.UUID) -> Any:
    row = (
        await session.execute(
            text(
                f"SELECT {_COLUMNS} FROM conversations c JOIN customers cu ON cu.id = c.customer_id"
                " WHERE c.id = :i"
            ),
            {"i": conversation_id},
        )
    ).first()
    if row is None:
        raise AppError("E-NF-001")
    return row


@router.get("/conversations")
async def list_conversations(
    status: ConversationStatus | None = None,
    limit: int = Query(default=30, ge=1, le=100),
    cursor: str | None = None,
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> Page:
    offset = decode_offset(cursor)
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as session:
        rows = (
            await session.execute(
                text(
                    f"SELECT {_COLUMNS} FROM conversations c JOIN customers cu ON cu.id = c.customer_id"
                    " WHERE (CAST(:s AS text) IS NULL OR c.status = :s)"
                    " ORDER BY c.last_message_at DESC NULLS LAST, c.id LIMIT :l OFFSET :o"
                ),
                {"s": status, "l": limit + 1, "o": offset},
            )
        ).all()
    page = rows[:limit]
    return Page(
        items=[conversation_out(r) for r in page],
        next_cursor=encode_offset(offset + limit) if len(rows) > limit else None,
    )


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: uuid.UUID,
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> ConversationDetailAny:
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as session:
        conv = await _conversation(session, conversation_id)
        messages = (
            await session.execute(
                text(
                    "SELECT m.id, m.direction, m.sender, m.content, m.created_at, a.status AS action_status"
                    " FROM messages m LEFT JOIN agent_actions a ON a.id = m.action_id"
                    " WHERE m.conversation_id = :c ORDER BY m.created_at, m.id"
                ),
                {"c": conversation_id},
            )
        ).all()
    base = conversation_out(conv)
    return ConversationDetailAny(
        **base.model_dump(),
        messages=[
            MessageOut(
                id=m.id,
                direction=m.direction,
                sender=m.sender,
                content=m.content,
                created_at=m.created_at,
                action_status=m.action_status,
            )
            for m in messages
        ],
    )


@router.post("/conversations/{conversation_id}/reply")
async def human_reply(
    conversation_id: uuid.UUID,
    body: HumanReplyIn,
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
    transport: httpx.AsyncBaseTransport | None = Depends(integrations.get_transport),  # noqa: B008
) -> MessageOut:
    tenant_id = principal.tenant_id
    if tenant_id is None:
        raise AppError("E-AUTH-007")
    async with session_scope(factory, tenant_id=tenant_id, user_id=principal.user_id) as session:
        await _conversation(session, conversation_id)
    senders = await build_senders(factory, tenant_id, transport)
    try:
        result = await engine.submit_reply(
            factory,
            senders,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            run_id=None,
            body=body.content,
            key=f"human:{uuid.uuid4()}",
            mode="auto",
            sender_role="human",
            decided_by=principal.user_id,
            human=True,
        )
    except engine.ActionDeferred as exc:
        raise AppError("E-ACT-004", detail=str(exc)) from exc
    engine.raise_for(result)
    async with session_scope(factory, tenant_id=tenant_id, user_id=principal.user_id) as session:
        await session.execute(
            text("UPDATE conversations SET human_active = true WHERE id = :c"),
            {"c": conversation_id},
        )
        message = (
            await session.execute(
                text(
                    "SELECT m.id, m.direction, m.sender, m.content, m.created_at, a.status AS action_status"
                    " FROM messages m JOIN agent_actions a ON a.id = m.action_id WHERE a.id = :a"
                ),
                {"a": result.action_id},
            )
        ).one()
    return MessageOut(
        id=message.id,
        direction=message.direction,
        sender=message.sender,
        content=message.content,
        created_at=message.created_at,
        action_status=message.action_status,
    )


@router.post("/conversations/{conversation_id}/resolve")
async def resolve_conversation(
    conversation_id: uuid.UUID,
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> ConversationOutAny:
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as session:
        await _conversation(session, conversation_id)
        await session.execute(
            text(
                "UPDATE handoffs SET status = 'resolved', resolved_at = now(), resolved_by = :u"
                " WHERE conversation_id = :c AND status IN ('open','claimed')"
            ),
            {"u": principal.user_id, "c": conversation_id},
        )
        await session.execute(
            text(
                "UPDATE conversations SET status = 'resolved', human_active = false WHERE id = :c"
            ),
            {"c": conversation_id},
        )
        await record(
            session,
            AuditEvent(
                action="conversation.resolved",
                entity_type="conversation",
                entity_id=str(conversation_id),
                actor_user_id=principal.user_id,
                actor_role=principal.role,
            ),
        )
        return conversation_out(await _conversation(session, conversation_id))


@router.post("/messages/{message_id}/report")
async def report_message(
    message_id: uuid.UUID,
    body: ReportIn,
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
    mailer: Mailer = Depends(get_mailer),  # noqa: B008
    transport: httpx.AsyncBaseTransport | None = Depends(integrations.get_transport),  # noqa: B008
) -> HandoffOut:
    tenant_id = principal.tenant_id
    if tenant_id is None:
        raise AppError("E-AUTH-007")
    async with session_scope(factory, tenant_id=tenant_id, user_id=principal.user_id) as session:
        message = (
            await session.execute(
                text(
                    "SELECT m.id, m.conversation_id, m.action_id FROM messages m"
                    " WHERE m.id = :m AND m.direction = 'out' AND m.sender = 'agent'"
                ),
                {"m": message_id},
            )
        ).first()
        if message is None:
            raise AppError("E-NF-001")
        conv = await _conversation(session, message.conversation_id)
        run_id = (
            await session.execute(
                text("SELECT run_id FROM agent_actions WHERE id = :a"), {"a": message.action_id}
            )
        ).scalar_one_or_none()
    summary = HandoffSummary(
        customer_masked_phone=mask_mobile(conv.phone_e164) if conv.phone_e164 else None,
        order_ref=conv.active_order_ref,
        customer_asked=body.reason[:400],
        agent_did=["Brand ne agent ka jawab galat bataya"],
        reason_code="CUSTOMER_REPORT",
        why_stuck="Brand ne jawab ko galat bataya.",
        language=conv.language or "hinglish",
    )
    decision = DecisionOut(
        action="handoff", reason_code="CUSTOMER_REPORT", priority="normal", handoff=summary
    )
    senders = await build_senders(factory, tenant_id, transport)
    handoff_id = await handoff_service.create_handoff(
        factory,
        mailer,
        senders,
        tenant_id=tenant_id,
        conversation_id=message.conversation_id,
        run_id=run_id,
        decision=decision,
        mode="auto",
    )
    async with session_scope(factory, tenant_id=tenant_id, user_id=principal.user_id) as session:
        if handoff_id is None:
            handoff_id = (
                await session.execute(
                    text(
                        "SELECT id FROM handoffs WHERE conversation_id = :c"
                        " AND status IN ('open','claimed')"
                    ),
                    {"c": message.conversation_id},
                )
            ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO answer_reports (message_id, action_id, reported_by, reason, handoff_id)"
                " VALUES (:m, :a, :u, :r, :h)"
            ),
            {
                "m": message_id,
                "a": message.action_id,
                "u": principal.user_id,
                "r": body.reason,
                "h": handoff_id,
            },
        )
        await session.execute(
            text("UPDATE conversations SET human_active = true WHERE id = :c"),
            {"c": message.conversation_id},
        )
        return await handoff_out(session, handoff_id)
