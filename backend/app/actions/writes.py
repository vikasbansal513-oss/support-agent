import hashlib
import secrets
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import timeutil
from app.core.audit import AuditEvent, record
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.errors import AppError
from app.core.flags import is_globally_paused
from app.integrations.shopify_write import (
    ShopifyWriter,
    WriteResult,
    WriteUnavailable,
    verify_effect,
)
from app.v1.contracts import TOOL_TIER, AddressIn, WriteTool
from app.v1.gate import GateDecision
from app.v1.policy import PolicyV2

Factory = async_sessionmaker[AsyncSession]
Regate = Callable[["ActionRow"], Awaitable[GateDecision]]

_COLS = (
    "id, conversation_id, tool_name, tool_args, order_ref, amount_paise, status, requires_approval,"
    " idempotency_key, confirm_token_hash, confirm_expires_at, attempts, risk_tier, error, external_ref"
)


@dataclass(frozen=True)
class ActionRow:
    id: uuid.UUID
    conversation_id: uuid.UUID
    tool: WriteTool
    args: dict[str, Any]
    order_ref: str
    amount_paise: int
    status: str
    requires_approval: bool
    key: str
    token_hash: str | None
    expires_at: datetime | None
    attempts: int
    error: str | None
    external_ref: str | None


@dataclass(frozen=True)
class Proposal:
    action: ActionRow
    token: str
    created: bool


@dataclass(frozen=True)
class WriteOutcome:
    action_id: uuid.UUID
    status: str
    detail: str | None = None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def action_key(conversation_id: uuid.UUID, tool: str, order_ref: str, n: int) -> str:
    return f"act:{conversation_id}:{tool}:{order_ref}:{n}"


def _row(raw: Any) -> ActionRow:
    return ActionRow(
        id=raw.id,
        conversation_id=raw.conversation_id,
        tool=raw.tool_name,
        args=raw.tool_args or {},
        order_ref=raw.order_ref,
        amount_paise=int(raw.amount_paise or 0),
        status=raw.status,
        requires_approval=raw.requires_approval,
        key=raw.idempotency_key,
        token_hash=raw.confirm_token_hash,
        expires_at=raw.confirm_expires_at,
        attempts=raw.attempts,
        error=raw.error,
        external_ref=raw.external_ref,
    )


async def get_action(session: AsyncSession, action_id: uuid.UUID) -> ActionRow | None:
    raw = (
        await session.execute(
            text(f"SELECT {_COLS} FROM agent_actions WHERE id = :i"),  # noqa: S608
            {"i": action_id},
        )
    ).first()
    return None if raw is None else _row(raw)


async def propose(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID | None,
    tool: WriteTool,
    order_ref: str,
    amount_paise: int,
    args: dict[str, Any],
    decision: GateDecision,
    policy: PolicyV2,
    write_count: int,
) -> Proposal:
    key = action_key(conversation_id, tool, order_ref, write_count + 1)
    token = secrets.token_urlsafe(16)
    expires = timeutil.utc_now() + timedelta(minutes=policy.confirm_ttl_minutes)
    raw = (
        await session.execute(
            text(
                "INSERT INTO agent_actions (run_id, conversation_id, action_type, idempotency_key,"
                " status, tool_name, tool_args, order_ref, amount_paise, risk_tier,"
                " requires_approval, confirm_token_hash, confirm_expires_at)"
                " VALUES (:r, :c, 'tool_call', :k, 'awaiting_customer', :t, CAST(:a AS jsonb), :o,"
                " :amt, :tier, :ra, :th, :ex)"
                " ON CONFLICT (tenant_id, idempotency_key) DO NOTHING RETURNING " + _COLS
            ),
            {
                "r": run_id,
                "c": conversation_id,
                "k": key,
                "t": tool,
                "a": _json(args),
                "o": order_ref,
                "amt": amount_paise,
                "tier": TOOL_TIER[tool],
                "ra": decision.route == "approval",
                "th": hash_token(token),
                "ex": expires,
            },
        )
    ).first()
    if raw is None:
        existing = (
            await session.execute(
                text(f"SELECT {_COLS} FROM agent_actions WHERE idempotency_key = :k"),  # noqa: S608
                {"k": key},
            )
        ).one()
        return Proposal(_row(existing), "", False)
    await record(
        session,
        AuditEvent(
            action="action.proposed",
            entity_type="agent_action",
            entity_id=str(raw.id),
            new_value={"tool": tool, "order_ref": order_ref, "amount_paise": amount_paise},
            actor_role="agent",
            run_id=str(run_id) if run_id else None,
        ),
    )
    return Proposal(_row(raw), token, True)


def _json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True)


async def confirm(
    session: AsyncSession, action_id: uuid.UUID, token: str, now: datetime | None = None
) -> ActionRow:
    moment = now or timeutil.utc_now()
    raw = (
        await session.execute(
            text(
                "UPDATE agent_actions SET confirm_token_hash = NULL, decided_at = now(),"
                " status = CASE WHEN requires_approval THEN 'pending_approval' ELSE 'approved' END"
                " WHERE id = :i AND status = 'awaiting_customer' AND confirm_token_hash = :h"
                " AND confirm_expires_at > :n RETURNING " + _COLS
            ),
            {"i": action_id, "h": hash_token(token), "n": moment},
        )
    ).first()
    if raw is None:
        raise AppError("E-ACT-005")
    await record(
        session,
        AuditEvent(
            action="action.customer_confirmed",
            entity_type="agent_action",
            entity_id=str(action_id),
            actor_role="customer",
        ),
    )
    return _row(raw)


async def expire_stale(factory: Factory, tenant_id: uuid.UUID) -> int:
    async with session_scope(factory, tenant_id=tenant_id) as session:
        rows = (
            await session.execute(
                text(
                    "UPDATE agent_actions SET status = 'expired', confirm_token_hash = NULL"
                    " WHERE status = 'awaiting_customer' AND confirm_expires_at <= :n RETURNING id,"
                    " conversation_id"
                ),
                {"n": timeutil.utc_now()},
            )
        ).all()
        for row in rows:
            await session.execute(
                text(
                    "UPDATE conversation_state SET awaiting = 'none', pending_action_id = NULL,"
                    " updated_at = now() WHERE conversation_id = :c AND pending_action_id = :a"
                ),
                {"c": row.conversation_id, "a": row.id},
            )
    return len(rows)


async def close_pending(
    factory: Factory, tenant_id: uuid.UUID, action_id: uuid.UUID, status: str, reason: str
) -> bool:
    async with session_scope(factory, tenant_id=tenant_id) as session:
        updated = await session.execute(
            text(
                "UPDATE agent_actions SET status = :s, error = :e, confirm_token_hash = NULL,"
                " decided_at = now() WHERE id = :i AND status = 'awaiting_customer' RETURNING id"
            ),
            {"s": status, "e": reason[:200], "i": action_id},
        )
        return updated.first() is not None


async def killed(session: AsyncSession, tool: str) -> bool:
    if await is_globally_paused(session):
        return True
    row = (
        await session.execute(
            text(
                "SELECT agent_paused, tool_kill -> CAST(:t AS text) AS killed FROM tenants"
                " WHERE id = app_current_tenant()"
            ),
            {"t": tool},
        )
    ).one()
    return bool(row.agent_paused) or row.killed is not None


async def customer_usage_today(
    session: AsyncSession, customer_id: uuid.UUID, tool: str, day: Any
) -> int:
    value = (
        await session.execute(
            text(
                "SELECT sum_paise FROM action_usage WHERE customer_id = :c AND day = :d AND tool = :t"
            ),
            {"c": customer_id, "d": day, "t": tool},
        )
    ).scalar_one_or_none()
    return int(value or 0)


async def brand_auto_value_today(session: AsyncSession, day: Any) -> int:
    value = (
        await session.execute(
            text("SELECT COALESCE(sum(sum_paise), 0) FROM action_usage WHERE day = :d"), {"d": day}
        )
    ).scalar_one()
    return int(value)


async def reserve_usage(
    session: AsyncSession,
    customer_id: uuid.UUID,
    tool: WriteTool,
    amount: int,
    policy: PolicyV2,
    day: Any,
    enforce: bool,
) -> bool:
    limit = policy.limits.refund_customer_daily_paise if tool == "refund_create" else None
    brand_cap = policy.limits.brand_daily_auto_value_paise
    if enforce:
        await session.execute(
            text("SELECT id FROM tenants WHERE id = app_current_tenant() FOR UPDATE")
        )
        if await brand_auto_value_today(session, day) + amount > brand_cap:
            return False
        if (
            limit is not None
            and await customer_usage_today(session, customer_id, tool, day) + amount > limit
        ):
            return False
    await session.execute(
        text(
            "INSERT INTO action_usage (customer_id, day, tool, count, sum_paise)"
            " VALUES (:c, :d, :t, 1, :a)"
            " ON CONFLICT (tenant_id, customer_id, day, tool)"
            " DO UPDATE SET count = action_usage.count + 1, sum_paise = action_usage.sum_paise + :a"
        ),
        {"c": customer_id, "d": day, "t": tool, "a": amount},
    )
    return True


async def release_usage(
    session: AsyncSession, customer_id: uuid.UUID, tool: str, amount: int, day: Any
) -> None:
    await session.execute(
        text(
            "UPDATE action_usage SET count = GREATEST(count - 1, 0),"
            " sum_paise = GREATEST(sum_paise - :a, 0)"
            " WHERE customer_id = :c AND day = :d AND tool = :t"
        ),
        {"c": customer_id, "d": day, "t": tool, "a": amount},
    )


async def _customer_of(session: AsyncSession, conversation_id: uuid.UUID) -> uuid.UUID:
    return uuid.UUID(
        str(
            (
                await session.execute(
                    text("SELECT customer_id FROM conversations WHERE id = :c"),
                    {"c": conversation_id},
                )
            ).scalar_one()
        )
    )


async def _call_writer(writer: ShopifyWriter, action: ActionRow) -> WriteResult:
    args = action.args
    if action.tool == "order_cancel":
        return await writer.cancel_order(
            action.order_ref, str(args.get("reason") or "customer request"), action.key
        )
    if action.tool == "refund_create":
        return await writer.create_refund(action.order_ref, action.amount_paise, action.key)
    if action.tool == "exchange_request":
        return await writer.create_exchange(
            action.order_ref, str(args["line_item"]), str(args["new_variant"]), action.key
        )
    return await writer.update_address(
        action.order_ref, AddressIn.model_validate(args["new_address"]), action.key
    )


async def _set(
    factory: Factory,
    tenant_id: uuid.UUID,
    action_id: uuid.UUID,
    status: str,
    *,
    error: str | None = None,
    external_ref: str | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    async with session_scope(factory, tenant_id=tenant_id) as session:
        await session.execute(
            text(
                "UPDATE agent_actions SET status = :s, error = :e, external_ref = COALESCE(:x, external_ref),"
                " tool_result = CASE WHEN CAST(:r AS text) IS NULL THEN tool_result ELSE CAST(:r AS jsonb) END,"
                " sent_at = CASE WHEN :s = 'confirmed' THEN now() ELSE sent_at END WHERE id = :i"
            ),
            {
                "s": status,
                "e": error,
                "x": external_ref,
                "r": _json(result) if result is not None else None,
                "i": action_id,
            },
        )
        await record(
            session,
            AuditEvent(
                action=f"action.{status}",
                entity_type="agent_action",
                entity_id=str(action_id),
                reason=error,
                actor_role="system",
            ),
        )


async def _execute(
    factory: Factory,
    writer: ShopifyWriter,
    tenant_id: uuid.UUID,
    action_id: uuid.UUID,
    policy: PolicyV2,
    *,
    enforce_limits: bool,
) -> WriteOutcome:
    day = timeutil.to_ist(timeutil.utc_now()).date()
    async with session_scope(factory, tenant_id=tenant_id) as session:
        claimed = (
            await session.execute(
                text(
                    "UPDATE agent_actions SET status = 'claimed', attempts = attempts + 1,"
                    " claimed_at = now() WHERE id = :i AND status = 'approved' RETURNING " + _COLS
                ),
                {"i": action_id},
            )
        ).first()
        if claimed is None:
            current = await get_action(session, action_id)
            return WriteOutcome(
                action_id, current.status if current else "missing", "ALREADY_HANDLED"
            )
        action = _row(claimed)
        customer_id = await _customer_of(session, action.conversation_id)
        if await killed(session, action.tool):
            await session.execute(
                text(
                    "UPDATE agent_actions SET status = 'rejected', error = 'KILLED' WHERE id = :i"
                ),
                {"i": action_id},
            )
            return WriteOutcome(action_id, "rejected", "KILLED")
        reserved = await reserve_usage(
            session, customer_id, action.tool, action.amount_paise, policy, day, enforce_limits
        )
        if not reserved:
            await session.execute(
                text(
                    "UPDATE agent_actions SET status = 'pending_approval', requires_approval = true,"
                    " error = 'LIMIT_EXCEEDED', attempts = attempts - 1 WHERE id = :i"
                ),
                {"i": action_id},
            )
            return WriteOutcome(action_id, "pending_approval", "LIMIT_EXCEEDED")
    try:
        before = await writer.read_order(action.order_ref)
        if before is None:
            raise WriteUnavailable("order unreadable")
        result = await _call_writer(writer, action)
        after = await writer.read_order(action.order_ref)
    except WriteUnavailable:
        await _release(factory, tenant_id, action, customer_id, day)
        await _set(factory, tenant_id, action_id, "needs_review", error="WRITE_UNAVAILABLE")
        return WriteOutcome(action_id, "needs_review", "WRITE_UNAVAILABLE")
    verified = after is not None and verify_effect(
        action.tool,
        before.refunded_paise,
        after,
        amount_paise=action.amount_paise,
        address_pin=(action.args.get("new_address") or {}).get("pin"),
    )
    if result.outcome == "rejected" and not verified:
        await _release(factory, tenant_id, action, customer_id, day)
        await _set(factory, tenant_id, action_id, "failed", error=f"REJECTED:{result.detail}")
        return WriteOutcome(action_id, "failed", "REJECTED")
    if verified:
        await _set(
            factory,
            tenant_id,
            action_id,
            "confirmed",
            external_ref=result.external_ref,
            result={"outcome": result.outcome},
        )
        return WriteOutcome(action_id, "confirmed")
    await _set(factory, tenant_id, action_id, "needs_review", error=f"UNVERIFIED:{result.outcome}")
    return WriteOutcome(action_id, "needs_review", f"UNVERIFIED:{result.outcome}")


async def execute(
    factory: Factory,
    writer: ShopifyWriter,
    tenant_id: uuid.UUID,
    action_id: uuid.UUID,
    policy: PolicyV2,
    *,
    enforce_limits: bool,
) -> WriteOutcome:
    outcome = await _execute(
        factory, writer, tenant_id, action_id, policy, enforce_limits=enforce_limits
    )
    if enforce_limits and outcome.status in ("confirmed", "needs_review"):
        async with session_scope(factory, tenant_id=tenant_id) as session:
            action = await get_action(session, action_id)
        if action is not None and not action.requires_approval:
            await check_anomaly(factory, tenant_id, action.tool)
    return outcome


async def _release(
    factory: Factory, tenant_id: uuid.UUID, action: ActionRow, customer_id: uuid.UUID, day: Any
) -> None:
    async with session_scope(factory, tenant_id=tenant_id) as session:
        await release_usage(session, customer_id, action.tool, action.amount_paise, day)


async def approve(
    factory: Factory,
    writer: ShopifyWriter,
    tenant_id: uuid.UUID,
    action_id: uuid.UUID,
    user_id: uuid.UUID,
    policy: PolicyV2,
    regate: Regate,
) -> WriteOutcome:
    async with session_scope(factory, tenant_id=tenant_id, user_id=user_id) as session:
        action = await get_action(session, action_id)
        if action is None:
            raise AppError("E-NF-001")
        if action.status != "pending_approval":
            raise AppError("E-ACT-005")
    decision = await regate(action)
    async with session_scope(factory, tenant_id=tenant_id, user_id=user_id) as session:
        if decision.route == "handoff":
            await session.execute(
                text(
                    "UPDATE agent_actions SET status = 'rejected', error = :e, decided_by = :u,"
                    " decided_at = now() WHERE id = :i AND status = 'pending_approval'"
                ),
                {"e": f"REGATE:{decision.reason}", "u": user_id, "i": action_id},
            )
            await record(
                session,
                AuditEvent(
                    action="action.approval_blocked",
                    entity_type="agent_action",
                    entity_id=str(action_id),
                    reason=decision.reason,
                    actor_user_id=user_id,
                    actor_role="owner",
                ),
            )
            return WriteOutcome(action_id, "rejected", decision.reason)
        updated = await session.execute(
            text(
                "UPDATE agent_actions SET status = 'approved', approved_by = :u, approved_at = now(),"
                " decided_by = :u, decided_at = now(), error = NULL, amount_paise = :a"
                " WHERE id = :i AND status = 'pending_approval' RETURNING id"
            ),
            {"u": user_id, "i": action_id, "a": decision.amount_paise},
        )
        if updated.first() is None:
            raise AppError("E-ACT-005")
        await record(
            session,
            AuditEvent(
                action="action.approved",
                entity_type="agent_action",
                entity_id=str(action_id),
                actor_user_id=user_id,
                actor_role="owner",
            ),
        )
    return await execute(factory, writer, tenant_id, action_id, policy, enforce_limits=False)


async def reject(
    factory: Factory, tenant_id: uuid.UUID, action_id: uuid.UUID, user_id: uuid.UUID, reason: str
) -> WriteOutcome:
    async with session_scope(factory, tenant_id=tenant_id, user_id=user_id) as session:
        updated = await session.execute(
            text(
                "UPDATE agent_actions SET status = 'rejected', error = 'REJECTED_BY_HUMAN',"
                " decided_by = :u, decided_at = now() WHERE id = :i"
                " AND status IN ('pending_approval','awaiting_customer') RETURNING id"
            ),
            {"u": user_id, "i": action_id},
        )
        if updated.first() is None:
            raise AppError("E-ACT-005")
        await record(
            session,
            AuditEvent(
                action="action.rejected",
                entity_type="agent_action",
                entity_id=str(action_id),
                reason=reason,
                actor_user_id=user_id,
                actor_role="owner",
            ),
        )
    return WriteOutcome(action_id, "rejected")


async def reconcile(
    factory: Factory, writer: ShopifyWriter, tenant_id: uuid.UUID, action_id: uuid.UUID
) -> WriteOutcome:
    async with session_scope(factory, tenant_id=tenant_id) as session:
        action = await get_action(session, action_id)
        if action is None or action.status != "needs_review":
            return WriteOutcome(action_id, action.status if action else "missing", "NOT_REVIEWABLE")
    try:
        after = await writer.read_order(action.order_ref)
    except WriteUnavailable:
        return WriteOutcome(action_id, "needs_review", "WRITE_UNAVAILABLE")
    if after is not None and verify_effect(
        action.tool,
        0,
        after,
        amount_paise=action.amount_paise,
        address_pin=(action.args.get("new_address") or {}).get("pin"),
    ):
        await _set(factory, tenant_id, action_id, "confirmed", error=None)
        return WriteOutcome(action_id, "confirmed", "RECONCILED")
    return WriteOutcome(action_id, "needs_review", "STILL_UNVERIFIED")


async def check_anomaly(factory: Factory, tenant_id: uuid.UUID, tool: WriteTool) -> bool:
    settings = get_settings()
    since = timeutil.utc_now() - timedelta(minutes=settings.anomaly_window_minutes)
    async with session_scope(factory, tenant_id=tenant_id) as session:
        count = int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM agent_actions WHERE tool_name = :t"
                        " AND requires_approval = false AND created_at >= :s"
                        " AND status IN ('claimed','executed','confirmed','needs_review')"
                    ),
                    {"t": tool, "s": since},
                )
            ).scalar_one()
        )
    if count < settings.anomaly_auto_threshold:
        return False
    await kill_tool(factory, tenant_id, tool, None, f"anomaly: {count} auto actions in window")
    return True


async def kill_tool(
    factory: Factory, tenant_id: uuid.UUID, tool: str, user_id: uuid.UUID | None, reason: str
) -> None:
    async with session_scope(factory, tenant_id=tenant_id, user_id=user_id) as session:
        await session.execute(
            text(
                "UPDATE tenants SET tool_kill = tool_kill || jsonb_build_object("
                "CAST(:t AS text), jsonb_build_object('at', now()::text, 'reason', CAST(:r AS text)))"
                " WHERE id = :i"
            ),
            {"t": tool, "r": reason[:200], "i": tenant_id},
        )
        await session.execute(
            text(
                "INSERT INTO notifications (audience, title, body, link)"
                " VALUES ('admin', 'Tool band kar diya gaya', :b, '/approvals')"
            ),
            {"b": f"{tool}: {reason[:200]}"},
        )
        await record(
            session,
            AuditEvent(
                action="tool.killed",
                entity_type="tool",
                entity_id=tool,
                reason=reason,
                actor_user_id=user_id,
                actor_role="owner" if user_id else "system",
            ),
        )


async def restore_tool(
    factory: Factory,
    tenant_id: uuid.UUID,
    tool: str,
    user_id: uuid.UUID,
    reason: str,
    actor_role: str = "owner",
) -> None:
    async with session_scope(factory, tenant_id=tenant_id, user_id=user_id) as session:
        await session.execute(
            text("UPDATE tenants SET tool_kill = tool_kill - CAST(:t AS text) WHERE id = :i"),
            {"t": tool, "i": tenant_id},
        )
        await record(
            session,
            AuditEvent(
                action="tool.restored",
                entity_type="tool",
                entity_id=tool,
                reason=reason,
                actor_user_id=user_id,
                actor_role=actor_role,
            ),
        )
