from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import usage as billing_usage

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


async def _threshold(session: AsyncSession, key: str, default: float) -> float:
    value = (
        await session.execute(
            text("SELECT value FROM monitoring_thresholds WHERE key = :k"), {"k": key}
        )
    ).scalar_one_or_none()
    return float(value) if value is not None else default


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

    review_recent = (
        await session.execute(
            text(
                "SELECT count(*) FROM jobs WHERE status = 'needs_review' AND updated_at >= now() - interval '15 minutes'"
            )
        )
    ).scalar_one()
    if review_recent > 5:
        await _upsert(
            session,
            Condition(
                kind="job_needs_review_spike",
                severity="warning",
                message=f"Pichle 15 minute me {review_recent} jobs ko review chahiye (needs_review).",
                dedupe_key="job_needs_review_spike",
            ),
        )
    else:
        await _resolve_missing(session, "job_needs_review_spike", None)

    failed_recent = (
        await session.execute(
            text(
                "SELECT count(*) FROM jobs WHERE status = 'failed' AND updated_at >= now() - interval '15 minutes'"
            )
        )
    ).scalar_one()
    failed_threshold = await _threshold(session, "failed_jobs_window_count", 5)
    if failed_recent > failed_threshold:
        await _upsert(
            session,
            Condition(
                kind="job_failure_spike",
                severity="critical",
                message=f"Pichle 15 minute me {failed_recent} jobs asli fail hue hain (status='failed').",
                dedupe_key="job_failure_spike",
            ),
        )
    else:
        await _resolve_missing(session, "job_failure_spike", None)

    queue_depth = (
        await session.execute(
            text("SELECT count(*) FROM jobs WHERE status IN ('queued', 'deferred')")
        )
    ).scalar_one()
    queue_threshold = await _threshold(session, "queue_backlog_jobs", 50)
    if queue_depth > queue_threshold:
        await _upsert(
            session,
            Condition(
                kind="queue_backlog",
                severity="warning",
                message=f"Queue me {queue_depth} jobs pending hain (threshold {int(queue_threshold)}).",
                dedupe_key="queue_backlog",
            ),
        )
    else:
        await _resolve_missing(session, "queue_backlog", None)

    accuracy_rows = (
        await session.execute(
            text(
                "SELECT a.tenant_id, t.name,"
                " count(*) FILTER (WHERE v.verdict IN ('correct','wrong','partial')"
                "   OR r.id IS NOT NULL) AS evaluated,"
                " count(*) FILTER (WHERE v.verdict = 'correct' AND r.id IS NULL) AS correct"
                " FROM agent_actions a JOIN tenants t ON t.id = a.tenant_id"
                " LEFT JOIN shadow_verdicts v ON v.action_id = a.id"
                " LEFT JOIN answer_reports r ON r.action_id = a.id"
                " WHERE a.action_type = 'reply_customer' AND a.created_at >= now() - interval '7 days'"
                " GROUP BY a.tenant_id, t.name HAVING count(*) > 0"
            )
        )
    ).all()
    acc_min_evaluated = await _threshold(session, "agent_accuracy_min_evaluated", 10)
    acc_min_percent = await _threshold(session, "agent_accuracy_min_percent", 80)
    for row in accuracy_rows:
        dedupe = f"agent_accuracy_low:{row.tenant_id}"
        if row.evaluated < acc_min_evaluated:
            await session.execute(
                text(
                    "UPDATE alerts SET status = 'resolved', resolved_at = now(), updated_at = now()"
                    " WHERE status = 'open' AND kind = 'agent_accuracy_low' AND dedupe_key = :d"
                ),
                {"d": dedupe},
            )
            continue
        pct = row.correct * 100 / row.evaluated
        if pct < acc_min_percent:
            await _upsert(
                session,
                Condition(
                    kind="agent_accuracy_low",
                    severity="critical" if pct < acc_min_percent / 2 else "warning",
                    message=f"{row.name} ka verified agent accuracy {round(pct, 1)}% hai"
                    f" (last 7 days, {row.evaluated} evaluated, threshold {acc_min_percent}%).",
                    dedupe_key=dedupe,
                    tenant_id=str(row.tenant_id),
                ),
            )
        else:
            await session.execute(
                text(
                    "UPDATE alerts SET status = 'resolved', resolved_at = now(), updated_at = now()"
                    " WHERE status = 'open' AND kind = 'agent_accuracy_low' AND dedupe_key = :d"
                ),
                {"d": dedupe},
            )

    slow_page_rows = (
        await session.execute(
            text(
                "SELECT p.tenant_id, t.name, p.route, avg(p.load_ms) AS avg_ms, count(*) AS n"
                " FROM page_perf_events p JOIN tenants t ON t.id = p.tenant_id"
                " WHERE p.load_ms IS NOT NULL AND p.created_at >= now() - interval '1 hour'"
                " GROUP BY p.tenant_id, t.name, p.route"
            )
        )
    ).all()
    slow_ms = await _threshold(session, "page_load_slow_ms", 4000)
    slow_min_samples = await _threshold(session, "page_load_min_samples", 5)
    for row in slow_page_rows:
        dedupe = f"page_load_slow:{row.tenant_id}:{row.route}"
        if row.n >= slow_min_samples and row.avg_ms > slow_ms:
            await _upsert(
                session,
                Condition(
                    kind="page_load_slow",
                    severity="warning",
                    message=f"{row.name} ka '{row.route}' page avg {round(row.avg_ms)} ms me"
                    f" load ho raha hai (last 1h, threshold {int(slow_ms)} ms).",
                    dedupe_key=dedupe,
                    tenant_id=str(row.tenant_id),
                ),
            )
        else:
            await session.execute(
                text(
                    "UPDATE alerts SET status = 'resolved', resolved_at = now(), updated_at = now()"
                    " WHERE status = 'open' AND kind = 'page_load_slow' AND dedupe_key = :d"
                ),
                {"d": dedupe},
            )

    db_conn_row = (
        await session.execute(
            text(
                "SELECT (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database())"
                " AS used, (SELECT setting::int FROM pg_settings WHERE name = 'max_connections')"
                " AS max"
            )
        )
    ).one()
    db_warn_pct = await _threshold(session, "db_connections_warn_percent", 80)
    db_pct = db_conn_row.used * 100 / db_conn_row.max if db_conn_row.max else 0
    if db_pct >= db_warn_pct:
        await _upsert(
            session,
            Condition(
                kind="db_connections_high",
                severity="critical" if db_pct >= 95 else "warning",
                message=f"DB connections {db_conn_row.used}/{db_conn_row.max}"
                f" ({round(db_pct, 1)}%, threshold {int(db_warn_pct)}%).",
                dedupe_key="db_connections_high",
            ),
        )
    else:
        await _resolve_missing(session, "db_connections_high", None)

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

    # Admin-configured per-client usage limits (messages/month, total cost/month, LLM
    # cost/month) -- see Billing > Alerts & Limits. Same numbers as
    # GET /admin/billing/usage-limits/status, computed once here and reused by that endpoint
    # would be ideal, but keeping this self-contained avoids a request-scoped session reuse
    # across modules; the SQL is cheap (one calendar month of data).
    limit_statuses = (await billing_usage.usage_limit_statuses(session)).statuses
    for status in limit_statuses:
        dedupe = f"usage_limit:{status.tenant_id}:{status.metric}"
        if status.breached_100:
            await _upsert(
                session,
                Condition(
                    kind="usage_limit_breach",
                    severity="critical",
                    message=f"{status.tenant_name} ne '{status.metric}' limit cross kar diya"
                    f" ({status.current_value}/{status.limit_value}, {status.percent_used}%).",
                    dedupe_key=dedupe,
                    tenant_id=str(status.tenant_id),
                ),
            )
        elif status.breached_90 or status.breached_70:
            await _upsert(
                session,
                Condition(
                    kind="usage_limit_breach",
                    severity="warning",
                    message=f"{status.tenant_name} apni '{status.metric}' limit ka"
                    f" {status.percent_used}% use kar chuka hai"
                    f" ({status.current_value}/{status.limit_value}).",
                    dedupe_key=dedupe,
                    tenant_id=str(status.tenant_id),
                ),
            )
        else:
            await session.execute(
                text(
                    "UPDATE alerts SET status = 'resolved', resolved_at = now(), updated_at = now()"
                    " WHERE status = 'open' AND kind = 'usage_limit_breach' AND dedupe_key = :d"
                ),
                {"d": dedupe},
            )
