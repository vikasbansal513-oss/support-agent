import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.actions import writes
from app.actions.senders import build_senders
from app.auth.deps import require_owner, require_user
from app.auth.service import Principal
from app.contracts.contracts import ReasonIn
from app.core import timeutil
from app.core.config import get_settings
from app.core.db import get_session_factory, session_scope
from app.core.errors import AppError
from app.core.mail import get_mailer
from app.integrations.shopify_write import get_writer
from app.v1 import outcome
from app.v1.contracts import WRITE_TOOLS
from app.v1.policy import PolicyV2, parse_any
from app.v1.regate import facts_for, regate

router = APIRouter(tags=["approvals"])
Factory = async_sessionmaker[AsyncSession]
ApprovalStatus = Literal[
    "awaiting_customer",
    "pending_approval",
    "approved",
    "claimed",
    "executed",
    "confirmed",
    "needs_review",
    "rejected",
    "failed",
    "expired",
]
_LIST = (
    "SELECT id, conversation_id, tool_name, order_ref, amount_paise, status, requires_approval,"
    " risk_tier, tool_args, error, external_ref, created_at, confirm_expires_at, approved_at"
    " FROM agent_actions WHERE tool_name = ANY(:tools)"
)


class ApprovalOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    tool: str
    order_ref: str | None
    amount_paise: int
    status: str
    requires_approval: bool
    risk_tier: str | None
    reason: str | None
    error: str | None
    external_ref: str | None
    created_at: datetime
    expires_at: datetime | None
    approved_at: datetime | None
    sla_due_at: datetime | None = None
    overdue: bool = False


class TimelineItem(BaseModel):
    at: datetime
    action: str
    actor_role: str | None
    reason: str | None


class ApprovalDetail(ApprovalOut):
    timeline: list[TimelineItem]


class OutcomeOut(BaseModel):
    action_id: uuid.UUID
    status: str
    detail: str | None


class ToolKillOut(BaseModel):
    tool: str
    killed: bool
    reason: str | None
    at: str | None


def _out(row: Any) -> ApprovalOut:
    args = row.tool_args or {}
    sla_due = None
    overdue = False
    if row.status == "pending_approval":
        sla_due = row.created_at + timedelta(minutes=get_settings().approval_sla_minutes)
        overdue = sla_due < timeutil.utc_now()
    return ApprovalOut(
        sla_due_at=sla_due,
        overdue=overdue,
        id=row.id,
        conversation_id=row.conversation_id,
        tool=row.tool_name,
        order_ref=row.order_ref,
        amount_paise=int(row.amount_paise or 0),
        status=row.status,
        requires_approval=row.requires_approval,
        risk_tier=row.risk_tier,
        reason=str(args["reason"])[:200] if args.get("reason") else None,
        error=row.error,
        external_ref=row.external_ref,
        created_at=row.created_at,
        expires_at=row.confirm_expires_at,
        approved_at=row.approved_at,
    )


async def _tell_customer(factory: Factory, tenant_id: uuid.UUID, action_id: uuid.UUID) -> None:
    mode = await _mode(factory, tenant_id)
    senders = await build_senders(factory, tenant_id)
    await outcome.notify(factory, senders, get_mailer(), tenant_id, action_id, mode)


async def _mode(factory: Factory, tenant_id: uuid.UUID) -> str:
    async with session_scope(factory, tenant_id=tenant_id) as session:
        return str(
            (
                await session.execute(
                    text("SELECT mode FROM tenants WHERE id = :t"), {"t": tenant_id}
                )
            ).scalar_one()
        )


def _tenant(principal: Principal) -> uuid.UUID:
    if principal.tenant_id is None:
        raise AppError("E-AUTH-007")
    return principal.tenant_id


async def _policy(factory: Factory, tenant_id: uuid.UUID) -> PolicyV2:
    async with session_scope(factory, tenant_id=tenant_id) as session:
        row = (
            await session.execute(text("SELECT content FROM policies WHERE status = 'published'"))
        ).first()
    policy = parse_any(row.content) if row else None
    if not isinstance(policy, PolicyV2):
        raise AppError("E-ACT-005")
    return policy


@router.get("/approvals")
async def list_approvals(
    status: ApprovalStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> list[ApprovalOut]:
    sql = _LIST + (" AND status = :s" if status else "") + " ORDER BY created_at DESC LIMIT :l"
    params: dict[str, Any] = {"tools": list(WRITE_TOOLS), "l": limit}
    if status:
        params["s"] = status
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as session:
        rows = (await session.execute(text(sql), params)).all()
    return [_out(r) for r in rows]


@router.get("/approvals/{action_id}")
async def approval_detail(
    action_id: uuid.UUID,
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> ApprovalDetail:
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as session:
        row = (
            await session.execute(
                text(
                    _LIST.replace(
                        "WHERE tool_name = ANY(:tools)", "WHERE id = :i AND tool_name = ANY(:tools)"
                    )
                ),
                {"i": action_id, "tools": list(WRITE_TOOLS)},
            )
        ).first()
        if row is None:
            raise AppError("E-NF-001")
        events = (
            await session.execute(
                text(
                    "SELECT occurred_at, action, actor_role, reason FROM audit_log"
                    " WHERE entity_type = 'agent_action' AND entity_id = :e ORDER BY occurred_at"
                ),
                {"e": str(action_id)},
            )
        ).all()
    base = _out(row).model_dump()
    return ApprovalDetail(
        **base,
        timeline=[
            TimelineItem(
                at=e.occurred_at, action=e.action, actor_role=e.actor_role, reason=e.reason
            )
            for e in events
        ],
    )


@router.post("/approvals/{action_id}/approve")
async def approve_action(
    action_id: uuid.UUID,
    principal: Principal = Depends(require_owner),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> OutcomeOut:
    tenant_id = _tenant(principal)
    user_id = principal.user_id
    policy = await _policy(factory, tenant_id)
    writer = get_writer()

    async def regate_fresh(action: writes.ActionRow) -> Any:
        facts = await facts_for(factory, tenant_id, action.conversation_id, writer)
        return await regate(factory, tenant_id, facts, policy, action)

    result = await writes.approve(
        factory, writer, tenant_id, action_id, user_id, policy, regate_fresh
    )
    await _tell_customer(factory, tenant_id, action_id)
    return OutcomeOut(action_id=result.action_id, status=result.status, detail=result.detail)


@router.post("/approvals/{action_id}/reject")
async def reject_action(
    action_id: uuid.UUID,
    body: ReasonIn,
    principal: Principal = Depends(require_owner),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> OutcomeOut:
    tenant_id = _tenant(principal)
    result = await writes.reject(factory, tenant_id, action_id, principal.user_id, body.reason)
    await _tell_customer(factory, tenant_id, action_id)
    return OutcomeOut(action_id=result.action_id, status=result.status, detail=result.detail)


@router.post("/approvals/{action_id}/reconcile")
async def reconcile_action(
    action_id: uuid.UUID,
    principal: Principal = Depends(require_owner),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> OutcomeOut:
    tenant_id = _tenant(principal)
    result = await writes.reconcile(factory, get_writer(), tenant_id, action_id)
    if result.detail == "RECONCILED":
        await _tell_customer(factory, tenant_id, action_id)
    return OutcomeOut(action_id=result.action_id, status=result.status, detail=result.detail)


@router.get("/tools/kill")
async def tool_kill_state(
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> list[ToolKillOut]:
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as session:
        kill = (
            await session.execute(
                text("SELECT tool_kill FROM tenants WHERE id = app_current_tenant()")
            )
        ).scalar_one()
    return [
        ToolKillOut(
            tool=tool,
            killed=tool in kill,
            reason=kill.get(tool, {}).get("reason"),
            at=kill.get(tool, {}).get("at"),
        )
        for tool in WRITE_TOOLS
    ]


def _tool(name: str) -> str:
    if name not in WRITE_TOOLS:
        raise AppError("E-NF-001")
    return name


@router.post("/tools/{tool}/kill")
async def kill_tool(
    tool: str,
    body: ReasonIn,
    principal: Principal = Depends(require_owner),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> ToolKillOut:
    await writes.kill_tool(factory, _tenant(principal), _tool(tool), principal.user_id, body.reason)
    return ToolKillOut(tool=tool, killed=True, reason=body.reason, at=None)


@router.post("/tools/{tool}/restore")
async def restore_tool(
    tool: str,
    body: ReasonIn,
    principal: Principal = Depends(require_owner),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> ToolKillOut:
    await writes.restore_tool(
        factory, _tenant(principal), _tool(tool), principal.user_id, body.reason
    )
    return ToolKillOut(tool=tool, killed=False, reason=None, at=None)
