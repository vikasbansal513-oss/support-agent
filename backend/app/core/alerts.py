from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_UPSERT = text(
    "INSERT INTO alerts (tenant_id, kind, severity, message, dedupe_key)"
    " VALUES (:t, :k, :s, :m, :d)"
    " ON CONFLICT (dedupe_key) WHERE status = 'open' DO UPDATE SET message = :m,"
    " updated_at = now()"
)


@dataclass(frozen=True)
class Condition:
    kind: str
    severity: str
    message: str
    dedupe_key: str
    tenant_id: str | None = None


async def _upsert(session: AsyncSession, cond: Condition) -> None:
    await session.execute(
        _UPSERT,
        {
            "t": cond.tenant_id,
            "k": cond.kind,
            "s": cond.severity,
            "m": cond.message,
            "d": cond.dedupe_key,
        },
    )


async def _resolve_missing(session: AsyncSession, kind: str, tenant_id: str | None) -> None:
    await session.execute(
        text(
            "UPDATE alerts SET status = 'resolved', resolved_at = now(), updated_at = now()"
            " WHERE status = 'open' AND kind = :k"
            " AND (CAST(:t AS uuid) IS NULL OR tenant_id = CAST(:t AS uuid))"
        ),
        {"k": kind, "t": tenant_id},
    )


async def scan_and_upsert(session: AsyncSession) -> None:
    worker_silent = (
        await session.execute(
            text(
                "SELECT (now() - last_seen_at) > interval '3 minutes' FROM heartbeats"
                " WHERE name = 'worker'"
            )
        )
    ).scalar_one_or_none()
    if worker_silent:
        await _upsert(
            session,
            Condition(
                kind="worker_silent",
                severity="critical",
                message="Worker 3 minute se heartbeat nahi bhej raha.",
                dedupe_key="worker_silent",
            ),
        )
    else:
        await _resolve_missing(session, "worker_silent", None)

    failed_recent = (
        await session.execute(
            text(
                "SELECT count(*) FROM jobs WHERE status = 'needs_review' AND updated_at >= now() - interval '15 minutes'"
            )
        )
    ).scalar_one()
    if failed_recent > 5:
        await _upsert(
            session,
            Condition(
                kind="job_failure_spike",
                severity="warning",
                message=f"Pichle 15 minute me {failed_recent} jobs fail hue.",
                dedupe_key="job_failure_spike",
            ),
        )
    else:
        await _resolve_missing(session, "job_failure_spike", None)

    llm_stats = (
        await session.execute(
            text(
                "SELECT count(*) AS total, count(*) FILTER (WHERE outcome <> 'ok') AS bad"
                " FROM llm_calls WHERE created_at >= now() - interval '30 minutes'"
            )
        )
    ).one()
    if llm_stats.total >= 10 and llm_stats.bad * 100 > llm_stats.total * 20:
        pct = round(llm_stats.bad * 100 / llm_stats.total, 1)
        await _upsert(
            session,
            Condition(
                kind="llm_failure_rate",
                severity="warning",
                message=f"Pichle 30 minute me LLM failure rate {pct}% hai.",
                dedupe_key="llm_failure_rate",
            ),
        )
    else:
        await _resolve_missing(session, "llm_failure_rate", None)

    breached = (
        await session.execute(
            text(
                "SELECT count(*) FROM handoffs WHERE status IN ('open','claimed')"
                " AND sla_due_at IS NOT NULL AND sla_due_at < now()"
            )
        )
    ).scalar_one()
    if breached > 0:
        await _upsert(
            session,
            Condition(
                kind="handoff_sla_breach",
                severity="warning",
                message=f"{breached} handoffs apni SLA cross kar gaye hain.",
                dedupe_key="handoff_sla_breach",
            ),
        )
    else:
        await _resolve_missing(session, "handoff_sla_breach", None)

    quota_rows = (
        await session.execute(
            text(
                "SELECT q.tenant_id, t.name, q.monthly_call_limit, q.warn_percent,"
                " (SELECT count(*) FROM llm_calls c WHERE c.tenant_id = q.tenant_id"
                "  AND c.created_at >= date_trunc('month', now())) AS calls"
                " FROM brand_quotas q JOIN tenants t ON t.id = q.tenant_id"
                " WHERE q.monthly_call_limit IS NOT NULL"
            )
        )
    ).all()
    for row in quota_rows:
        pct = row.calls * 100 / row.monthly_call_limit if row.monthly_call_limit else 0
        dedupe = f"quota:{row.tenant_id}"
        if pct >= 100:
            await _upsert(
                session,
                Condition(
                    kind="quota_breach",
                    severity="critical",
                    message=f"{row.name} ne monthly AI call quota cross kar diya ({row.calls}/{row.monthly_call_limit}).",
                    dedupe_key=dedupe,
                    tenant_id=str(row.tenant_id),
                ),
            )
        elif pct >= row.warn_percent:
            await _upsert(
                session,
                Condition(
                    kind="quota_breach",
                    severity="warning",
                    message=f"{row.name} apni quota ka {round(pct)}% use kar chuka hai ({row.calls}/{row.monthly_call_limit}).",
                    dedupe_key=dedupe,
                    tenant_id=str(row.tenant_id),
                ),
            )
        else:
            await session.execute(
                text(
                    "UPDATE alerts SET status = 'resolved', resolved_at = now(), updated_at = now()"
                    " WHERE status = 'open' AND kind = 'quota_breach' AND dedupe_key = :d"
                ),
                {"d": dedupe},
            )
