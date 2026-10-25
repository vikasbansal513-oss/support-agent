import uuid
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import require_admin
from app.auth.service import Principal
from app.contracts.contracts import LlmUsageRowOut
from app.core import timeutil
from app.core.db import get_session_factory, session_scope

router = APIRouter(prefix="/admin", tags=["admin-llm"])
Factory = async_sessionmaker[AsyncSession]


@router.get("/llm-usage")
async def llm_usage(
    tenant_id: uuid.UUID | None = None,
    days: int = Query(default=7, ge=1, le=90),
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> list[LlmUsageRowOut]:
    since: date = timeutil.to_ist(timeutil.utc_now()).date() - timedelta(days=days - 1)
    params: dict[str, Any] = {"since": since, "tenant": str(tenant_id) if tenant_id else None}
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT tenant_id, to_char(created_at AT TIME ZONE 'Asia/Kolkata', 'YYYY-MM-DD') AS day,"
                    " count(*) AS calls, COALESCE(sum(input_tokens), 0) AS input_tokens,"
                    " COALESCE(sum(output_tokens), 0) AS output_tokens,"
                    " sum(est_cost_micro) AS est_cost_micro,"
                    " CAST(percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms) AS integer)"
                    " AS p95_latency_ms FROM llm_calls"
                    " WHERE (created_at AT TIME ZONE 'Asia/Kolkata')::date >= :since"
                    " AND (CAST(:tenant AS uuid) IS NULL OR tenant_id = CAST(:tenant AS uuid))"
                    " GROUP BY tenant_id, day ORDER BY day DESC, tenant_id"
                ),
                params,
            )
        ).all()
    return [
        LlmUsageRowOut(
            tenant_id=r.tenant_id,
            day=r.day,
            calls=int(r.calls),
            input_tokens=int(r.input_tokens),
            output_tokens=int(r.output_tokens),
            est_cost_micro=int(r.est_cost_micro) if r.est_cost_micro is not None else None,
            p95_latency_ms=r.p95_latency_ms,
        )
        for r in rows
    ]
