from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import require_user
from app.auth.service import Principal
from app.billing import usage as billing_usage
from app.billing.usage_schemas import ClientUsageOut
from app.channels.identity import usage_period
from app.contracts.contracts import DashboardStatsOut, UsageOut
from app.core import timeutil
from app.core.db import get_session_factory, session_scope

router = APIRouter(tags=["dashboard"])
Factory = async_sessionmaker[AsyncSession]

_DAY_START = "date_trunc('day', now() AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata'"


@router.get("/dashboard/stats")
async def stats(
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> DashboardStatsOut:
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as session:
        row = (
            await session.execute(
                text(
                    f"""SELECT
  (SELECT count(*) FROM messages WHERE direction = 'in' AND created_at >= {_DAY_START}) AS messages_today,
  (SELECT count(*) FROM agent_runs WHERE status = 'done' AND decision->>'action' = 'reply'
     AND started_at >= {_DAY_START}) AS handled,
  (SELECT count(*) FROM handoffs WHERE would_handoff = false AND created_at >= {_DAY_START}) AS handed_off,
  (SELECT count(*) FROM handoffs WHERE would_handoff = false AND status IN ('open','claimed')) AS open_handoffs,
  (SELECT count(*) FROM shadow_verdicts v JOIN agent_actions a ON a.id = v.action_id
     WHERE a.status IN ('shadow','draft','sent') AND v.verdict = 'correct') AS correct,
  (SELECT count(*) FROM shadow_verdicts v JOIN agent_actions a ON a.id = v.action_id
     WHERE a.status IN ('shadow','draft','sent') AND v.verdict = 'wrong') AS wrong,
  (SELECT count(*) FROM agent_actions a LEFT JOIN shadow_verdicts v ON v.action_id = a.id
     WHERE a.action_type = 'reply_customer' AND a.status = 'shadow' AND v.id IS NULL) AS unreviewed,
  (SELECT mode FROM tenants WHERE id = app_current_tenant()) AS mode,
  (SELECT agent_paused FROM tenants WHERE id = app_current_tenant()) AS paused"""
                )
            )
        ).one()
    return DashboardStatsOut(
        day_ist=timeutil.to_ist(timeutil.utc_now()).date().isoformat(),
        messages_today=row.messages_today,
        handled_by_agent_today=row.handled,
        handed_off_today=row.handed_off,
        open_handoffs=row.open_handoffs,
        shadow_correct=row.correct,
        shadow_wrong=row.wrong,
        shadow_unreviewed=row.unreviewed,
        mode=row.mode,
        agent_paused=row.paused,
    )


@router.get("/usage")
async def usage(
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> UsageOut:
    period = usage_period()
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as session:
        tenant = (
            await session.execute(
                text(
                    "SELECT included_conversations, usage_override_limit FROM tenants"
                    " WHERE id = app_current_tenant()"
                )
            )
        ).one()
        count = (
            await session.execute(
                text("SELECT conversation_count FROM usage_counters WHERE period_start = :p"),
                {"p": period},
            )
        ).scalar_one_or_none()
    limit = (
        tenant.usage_override_limit
        if tenant.usage_override_limit is not None
        else tenant.included_conversations
    )
    used = int(count or 0)
    return UsageOut(
        period_start=period.isoformat(),
        conversation_count=used,
        limit=limit,
        limit_reached=used >= limit,
    )


@router.get("/dashboard/billing-usage")
async def billing_usage_view(
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> ClientUsageOut:
    """Client's own current-month usage + cost, for the Dashboard. Reads cross-tenant-admin
    tables (gcp_usage_events, plivo_call_records, speech_usage_records, invoices, billing
    settings) under a super-admin session because those tables are admin-only RLS, but every
    query inside billing_usage.client_usage() is filtered to `principal.tenant_id` -- a client
    can never see another tenant's numbers through this endpoint."""
    async with session_scope(factory, super_admin=True, user_id=principal.user_id) as session:
        return await billing_usage.client_usage(session, principal.tenant_id)
