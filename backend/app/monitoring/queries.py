"""Control Tower monitoring: agent task history, accuracy, page performance, traffic.

Every number here is read from existing tables (agent_runs, agent_actions, llm_calls,
shadow_verdicts, answer_reports, external_calls, page_perf_events, jobs, tenants,
audit_log) or the current Postgres/process state (pg_stat_activity, resource.getrusage).
Nothing is simulated or hand-picked; see DECISIONS.md 2026-11-02 for what each number
can and cannot tell you (in particular: process-level CPU/RSS, not whole-Cloud-Run-fleet
CPU/memory, and integration-call rate, not raw inbound HTTP request rate).
"""

import resource
import uuid
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.contracts import (
    AccuracyBucketOut,
    AccuracyOut,
    AccuracyTenantOut,
    AgentTaskActionOut,
    AgentTaskDetailOut,
    AgentTaskListOut,
    AgentTaskLlmCallOut,
    AgentTaskRowOut,
    ClientTrafficRowOut,
    HistoricalOut,
    HistoricalPointOut,
    HistoricalSeriesOut,
    PagePerfOut,
    PagePerfRowOut,
    TrafficOut,
)

_CATEGORY_SQL = (
    "COALESCE(r.decision->'reply'->>'template_id',"
    " CASE WHEN r.decision->'handoff' IS NOT NULL"
    "   THEN 'handoff:' || COALESCE(r.decision->'handoff'->>'reason_code', 'unknown') END,"
    " r.decision->>'action', 'unknown')"
)
_CORRECTNESS_SQL = (
    "CASE"
    " WHEN rep.report_count > 0 THEN 'wrong'"
    " WHEN v.verdict = 'wrong' THEN 'wrong'"
    " WHEN v.verdict = 'partial' THEN 'partial'"
    " WHEN v.verdict = 'correct' THEN 'correct'"
    " ELSE 'unverified' END"
)


async def _threshold(session: AsyncSession, key: str, default: float) -> float:
    value = (
        await session.execute(
            text("SELECT value FROM monitoring_thresholds WHERE key = :k"), {"k": key}
        )
    ).scalar_one_or_none()
    return float(value) if value is not None else default


async def agent_task_list(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | None,
    category: str | None,
    status: str | None,
    date_from: str | None,
    date_to: str | None,
    page: int,
    page_size: int,
) -> AgentTaskListOut:
    where = (
        "WHERE (CAST(:tenant AS uuid) IS NULL OR r.tenant_id = CAST(:tenant AS uuid))"
        " AND (CAST(:status AS text) IS NULL OR r.status = :status)"
        " AND (CAST(:d_from AS timestamptz) IS NULL OR r.started_at >= CAST(:d_from AS timestamptz))"
        " AND (CAST(:d_to AS timestamptz) IS NULL OR r.started_at <= CAST(:d_to AS timestamptz))"
        f" AND (CAST(:category AS text) IS NULL OR {_CATEGORY_SQL} = :category)"
    )
    params: dict[str, Any] = {
        "tenant": str(tenant_id) if tenant_id else None,
        "status": status,
        "d_from": date_from,
        "d_to": date_to,
        "category": category,
    }
    total = (
        await session.execute(text(f"SELECT count(*) FROM agent_runs r {where}"), params)
    ).scalar_one()
    rows = (
        await session.execute(
            text(
                "SELECT r.id AS run_id, r.tenant_id, t.name AS tenant_name, r.started_at,"
                " r.finished_at, r.status,"
                f" {_CATEGORY_SQL} AS task_category,"
                " EXISTS (SELECT 1 FROM handoffs h WHERE h.run_id = r.id) AS handed_off,"
                f" {_CORRECTNESS_SQL} AS correctness,"
                " lc.llm_cost_micro, lc.llm_model"
                " FROM agent_runs r"
                " JOIN tenants t ON t.id = r.tenant_id"
                " LEFT JOIN agent_actions a ON a.run_id = r.id AND a.action_type = 'reply_customer'"
                " LEFT JOIN shadow_verdicts v ON v.action_id = a.id"
                " LEFT JOIN LATERAL ("
                "   SELECT count(*) AS report_count FROM answer_reports rp WHERE rp.action_id = a.id"
                " ) rep ON true"
                " LEFT JOIN LATERAL ("
                "   SELECT sum(c.est_cost_micro) AS llm_cost_micro,"
                "     (array_agg(c.model ORDER BY c.created_at DESC))[1] AS llm_model"
                "   FROM llm_calls c WHERE c.run_id = r.id"
                " ) lc ON true"
                f" {where}"
                " ORDER BY r.started_at DESC LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": page_size, "offset": (page - 1) * page_size},
        )
    ).all()
    items = [
        AgentTaskRowOut(
            run_id=r.run_id,
            tenant_id=r.tenant_id,
            tenant_name=r.tenant_name,
            started_at=r.started_at,
            finished_at=r.finished_at,
            duration_ms=(
                int((r.finished_at - r.started_at).total_seconds() * 1000)
                if r.finished_at
                else None
            ),
            task_category=r.task_category,
            status=r.status,
            correctness=r.correctness,
            handed_off=bool(r.handed_off),
            llm_cost_micro=int(r.llm_cost_micro) if r.llm_cost_micro is not None else None,
            llm_model=r.llm_model,
        )
        for r in rows
    ]
    return AgentTaskListOut(items=items, total=int(total))


async def agent_task_detail(session: AsyncSession, run_id: uuid.UUID) -> AgentTaskDetailOut | None:
    run = (
        await session.execute(
            text(
                "SELECT r.id, r.tenant_id, t.name AS tenant_name, r.status, r.started_at,"
                " r.finished_at, r.error_id, r.trace, r.decision"
                " FROM agent_runs r JOIN tenants t ON t.id = r.tenant_id WHERE r.id = :i"
            ),
            {"i": run_id},
        )
    ).one_or_none()
    if run is None:
        return None
    actions = (
        await session.execute(
            text(
                "SELECT action_type, status, tool_name, body_text, error, created_at"
                " FROM agent_actions WHERE run_id = :i ORDER BY created_at"
            ),
            {"i": run_id},
        )
    ).all()
    calls = (
        await session.execute(
            text(
                "SELECT purpose, provider, model, input_tokens, output_tokens, latency_ms,"
                " outcome, created_at FROM llm_calls WHERE run_id = :i ORDER BY created_at"
            ),
            {"i": run_id},
        )
    ).all()
    verdict_row = (
        await session.execute(
            text(
                "SELECT v.verdict, rp.reason FROM agent_actions a"
                " LEFT JOIN shadow_verdicts v ON v.action_id = a.id"
                " LEFT JOIN answer_reports rp ON rp.action_id = a.id"
                " WHERE a.run_id = :i AND a.action_type = 'reply_customer'"
                " ORDER BY a.created_at DESC LIMIT 1"
            ),
            {"i": run_id},
        )
    ).one_or_none()
    return AgentTaskDetailOut(
        run_id=run.id,
        tenant_id=run.tenant_id,
        tenant_name=run.tenant_name,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error_id=run.error_id,
        trace=run.trace or [],
        decision=run.decision,
        actions=[
            AgentTaskActionOut(
                action_type=a.action_type,
                status=a.status,
                tool_name=a.tool_name,
                body_text=a.body_text,
                error=a.error,
                created_at=a.created_at,
            )
            for a in actions
        ],
        llm_calls=[
            AgentTaskLlmCallOut(
                purpose=c.purpose,
                provider=c.provider,
                model=c.model,
                input_tokens=c.input_tokens,
                output_tokens=c.output_tokens,
                latency_ms=c.latency_ms,
                outcome=c.outcome,
                created_at=c.created_at,
            )
            for c in calls
        ],
        verdict=verdict_row.verdict if verdict_row else None,
        report_reason=verdict_row.reason if verdict_row else None,
    )


def _bucket(row: Any) -> AccuracyBucketOut:
    total_actions = int(row.total_actions)
    sent = int(row.sent)
    evaluated = int(row.evaluated)
    correct = int(row.correct)
    partial = int(row.partial)
    wrong = int(row.wrong)
    unverified = int(row.unverified)
    reported = int(row.reported)
    runs_total = int(row.runs_total)
    handed_off = int(row.handed_off)
    return AccuracyBucketOut(
        total_evaluated=evaluated,
        correct=correct,
        incorrect=wrong,
        partially_correct=partial,
        unverified=unverified,
        accuracy_percent=round(correct * 100 / evaluated, 1) if evaluated else 0.0,
        task_success_percent=round(sent * 100 / total_actions, 1) if total_actions else 0.0,
        human_correction_percent=round(reported * 100 / sent, 1) if sent else 0.0,
        handoff_percent=round(handed_off * 100 / runs_total, 1) if runs_total else 0.0,
    )


_ACCURACY_CTE = (
    "WITH scoped AS ("
    "  SELECT a.id, a.tenant_id, a.status,"
    f"    {_CORRECTNESS_SQL} AS correctness,"
    "    (rep.report_count > 0) AS reported"
    "  FROM agent_actions a"
    "  LEFT JOIN shadow_verdicts v ON v.action_id = a.id"
    "  LEFT JOIN LATERAL ("
    "    SELECT count(*) AS report_count FROM answer_reports rp WHERE rp.action_id = a.id"
    "  ) rep ON true"
    "  WHERE a.action_type = 'reply_customer' AND a.created_at >= now() - interval '{window}'"
    ")"
)


async def accuracy(
    session: AsyncSession, window: Literal["24h", "7d", "30d"] = "7d"
) -> AccuracyOut:
    interval = {"24h": "24 hours", "7d": "7 days", "30d": "30 days"}[window]
    cte = _ACCURACY_CTE.format(window=interval)
    overall_row = (
        await session.execute(
            text(
                f"{cte} SELECT"
                " count(*) AS total_actions,"
                " count(*) FILTER (WHERE status = 'sent') AS sent,"
                " count(*) FILTER (WHERE correctness <> 'unverified') AS evaluated,"
                " count(*) FILTER (WHERE correctness = 'correct') AS correct,"
                " count(*) FILTER (WHERE correctness = 'partial') AS partial,"
                " count(*) FILTER (WHERE correctness = 'wrong') AS wrong,"
                " count(*) FILTER (WHERE correctness = 'unverified') AS unverified,"
                " count(*) FILTER (WHERE reported) AS reported,"
                " (SELECT count(*) FROM agent_runs WHERE started_at >= now() - interval"
                f" '{interval}') AS runs_total,"
                " (SELECT count(DISTINCT run_id) FROM handoffs WHERE created_at >= now()"
                f" - interval '{interval}' AND run_id IS NOT NULL) AS handed_off"
                " FROM scoped"
            )
        )
    ).one()
    by_tenant_rows = (
        await session.execute(
            text(
                f"{cte} SELECT s.tenant_id, t.name AS tenant_name,"
                " count(*) AS total_actions,"
                " count(*) FILTER (WHERE s.status = 'sent') AS sent,"
                " count(*) FILTER (WHERE s.correctness <> 'unverified') AS evaluated,"
                " count(*) FILTER (WHERE s.correctness = 'correct') AS correct,"
                " count(*) FILTER (WHERE s.correctness = 'partial') AS partial,"
                " count(*) FILTER (WHERE s.correctness = 'wrong') AS wrong,"
                " count(*) FILTER (WHERE s.correctness = 'unverified') AS unverified,"
                " count(*) FILTER (WHERE s.reported) AS reported,"
                " (SELECT count(*) FROM agent_runs r WHERE r.tenant_id = s.tenant_id"
                f"   AND r.started_at >= now() - interval '{interval}') AS runs_total,"
                " (SELECT count(DISTINCT h.run_id) FROM handoffs h WHERE h.tenant_id = s.tenant_id"
                f"   AND h.created_at >= now() - interval '{interval}' AND h.run_id IS NOT NULL)"
                "   AS handed_off"
                " FROM scoped s JOIN tenants t ON t.id = s.tenant_id"
                " GROUP BY s.tenant_id, t.name HAVING count(*) > 0"
            )
        )
    ).all()
    return AccuracyOut(
        overall=_bucket(overall_row),
        by_tenant=[
            AccuracyTenantOut(
                tenant_id=r.tenant_id, tenant_name=r.tenant_name, **_bucket(r).model_dump()
            )
            for r in by_tenant_rows
        ],
    )


async def page_performance(
    session: AsyncSession, tenant_id: uuid.UUID | None, days: int = 7
) -> PagePerfOut:
    slow_ms = await _threshold(session, "page_load_slow_ms", 4000)
    rows = (
        await session.execute(
            text(
                "SELECT p.tenant_id, t.name AS tenant_name, p.route, count(*) AS samples,"
                " avg(p.load_ms) AS avg_load_ms,"
                " percentile_disc(0.5) WITHIN GROUP (ORDER BY p.load_ms) AS p50_load_ms,"
                " percentile_disc(0.95) WITHIN GROUP (ORDER BY p.load_ms) AS p95_load_ms,"
                " percentile_disc(0.99) WITHIN GROUP (ORDER BY p.load_ms) AS p99_load_ms,"
                " avg(p.lcp_ms) AS avg_lcp_ms, avg(p.ttfb_ms) AS avg_ttfb_ms,"
                " count(*) FILTER (WHERE p.failed_api) AS failed_api_count"
                " FROM page_perf_events p JOIN tenants t ON t.id = p.tenant_id"
                " WHERE p.created_at >= now() - (:days || ' days')::interval"
                " AND (CAST(:tenant AS uuid) IS NULL OR p.tenant_id = CAST(:tenant AS uuid))"
                " AND p.load_ms IS NOT NULL"
                " GROUP BY p.tenant_id, t.name, p.route"
            ),
            {"days": days, "tenant": str(tenant_id) if tenant_id else None},
        )
    ).all()
    out_rows = [
        PagePerfRowOut(
            tenant_id=r.tenant_id,
            tenant_name=r.tenant_name,
            route=r.route,
            samples=int(r.samples),
            avg_load_ms=round(float(r.avg_load_ms), 1) if r.avg_load_ms is not None else None,
            p50_load_ms=int(r.p50_load_ms) if r.p50_load_ms is not None else None,
            p95_load_ms=int(r.p95_load_ms) if r.p95_load_ms is not None else None,
            p99_load_ms=int(r.p99_load_ms) if r.p99_load_ms is not None else None,
            avg_lcp_ms=round(float(r.avg_lcp_ms), 1) if r.avg_lcp_ms is not None else None,
            avg_ttfb_ms=round(float(r.avg_ttfb_ms), 1) if r.avg_ttfb_ms is not None else None,
            failed_api_count=int(r.failed_api_count),
            status="slow" if (r.avg_load_ms or 0) > slow_ms else "good",
        )
        for r in rows
    ]
    slowest = max(out_rows, key=lambda r: r.avg_load_ms or 0, default=None)
    return PagePerfOut(
        rows=out_rows,
        slowest_client=slowest.tenant_name if slowest else None,
        slowest_page=slowest.route if slowest else None,
        slow_threshold_ms=int(slow_ms),
    )


def _process_rss_mb() -> float:
    # ru_maxrss is KB on Linux, bytes on macOS; this app only ever runs on the Linux
    # Cloud Run image, so KB -> MB is the correct conversion here.
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


async def traffic(session: AsyncSession) -> TrafficOut:
    tenants_row = (
        await session.execute(
            text(
                "SELECT count(*) AS total,"
                " count(*) FILTER (WHERE status = 'active') AS active FROM tenants"
            )
        )
    ).one()
    active_users = (
        await session.execute(
            text(
                "SELECT count(DISTINCT actor_user_id) FROM audit_log"
                " WHERE occurred_at >= now() - interval '5 minutes' AND actor_user_id IS NOT NULL"
            )
        )
    ).scalar_one()
    jobs_row = (
        await session.execute(
            text(
                "SELECT count(*) FILTER (WHERE status = 'processing') AS running,"
                " count(*) FILTER (WHERE status IN ('queued','deferred')) AS queue,"
                " count(*) FILTER (WHERE status = 'needs_review'"
                "   AND updated_at >= now() - interval '1 hour') AS needs_review,"
                " count(*) FILTER (WHERE status = 'failed'"
                "   AND updated_at >= now() - interval '1 hour') AS failed FROM jobs"
            )
        )
    ).one()
    db_conn = (
        await session.execute(
            text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
        )
    ).scalar_one()
    integ = (
        await session.execute(
            text(
                "SELECT count(*) AS n, avg(latency_ms) AS avg_ms,"
                " percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,"
                " count(*) FILTER (WHERE http_status >= 400 OR error_class IS NOT NULL) AS errors"
                " FROM external_calls WHERE created_at >= now() - interval '1 hour'"
            )
        )
    ).one()
    by_client_rows = (
        await session.execute(
            text(
                "SELECT t.id AS tenant_id, t.name,"
                " (SELECT count(*) FROM agent_runs r WHERE r.tenant_id = t.id"
                "   AND r.started_at >= now() - interval '24 hours') AS agent_runs_24h,"
                " (SELECT count(*) FROM external_calls e WHERE e.tenant_id = t.id"
                "   AND e.created_at >= now() - interval '1 hour') AS integration_calls_1h,"
                " (SELECT avg(e.latency_ms) FROM external_calls e WHERE e.tenant_id = t.id"
                "   AND e.created_at >= now() - interval '1 hour') AS avg_latency_ms,"
                " (SELECT count(*) FROM external_calls e WHERE e.tenant_id = t.id"
                "   AND e.created_at >= now() - interval '1 hour'"
                "   AND (e.http_status >= 400 OR e.error_class IS NOT NULL)) AS failed_calls_1h"
                " FROM tenants t WHERE t.status = 'active'"
                " ORDER BY agent_runs_24h DESC LIMIT 20"
            )
        )
    ).all()
    return TrafficOut(
        total_clients=int(tenants_row.total),
        active_clients=int(tenants_row.active),
        active_users_5m=int(active_users),
        running_jobs=int(jobs_row.running),
        queue_depth=int(jobs_row.queue),
        needs_review_jobs_recent=int(jobs_row.needs_review),
        failed_jobs_recent=int(jobs_row.failed),
        db_connections=int(db_conn),
        integration_calls_per_minute=round(int(integ.n) / 60, 2),
        avg_integration_latency_ms=round(float(integ.avg_ms), 1)
        if integ.avg_ms is not None
        else None,
        p95_integration_latency_ms=int(integ.p95_ms) if integ.p95_ms is not None else None,
        integration_error_rate_percent=(round(integ.errors * 100 / integ.n, 1) if integ.n else 0.0),
        process_rss_mb=_process_rss_mb(),
        by_client=[
            ClientTrafficRowOut(
                tenant_id=r.tenant_id,
                name=r.name,
                agent_runs_24h=int(r.agent_runs_24h),
                integration_calls_1h=int(r.integration_calls_1h),
                avg_integration_latency_ms=(
                    round(float(r.avg_latency_ms), 1) if r.avg_latency_ms is not None else None
                ),
                failed_integration_calls_1h=int(r.failed_calls_1h),
            )
            for r in by_client_rows
        ],
        note=(
            "integration_calls_per_minute/latency sirf Shopify/courier/WhatsApp/Gmail jaisi"
            " outbound integration calls se hain (external_calls table), raw inbound HTTP"
            " request count se nahi (wo abhi instrumented nahi hai). process_rss_mb is API"
            " process ka hai, pure Cloud Run fleet ka nahi (Cloud Monitoring API wire nahi"
            " hui hai). active_users_5m audit_log actor_user_id se hai (actual action liya"
            " hua), har open tab ka exact concurrent-session count nahi."
        ),
    )


async def historical(
    session: AsyncSession, range_: Literal["24h", "7d", "30d"], tenant_id: uuid.UUID | None
) -> HistoricalOut:
    bucket_expr, interval = {
        "24h": ("to_char(ts AT TIME ZONE 'Asia/Kolkata', 'HH24:00')", "24 hours"),
        "7d": ("to_char(ts AT TIME ZONE 'Asia/Kolkata', 'YYYY-MM-DD')", "7 days"),
        "30d": ("to_char(ts AT TIME ZONE 'Asia/Kolkata', 'YYYY-MM-DD')", "30 days"),
    }[range_]
    tenant_filter_runs = "AND tenant_id = CAST(:tenant AS uuid)" if tenant_id else ""
    params = {"tenant": str(tenant_id) if tenant_id else None}

    async def series(name: str, sql: str) -> HistoricalSeriesOut:
        rows = (await session.execute(text(sql), params)).all()
        return HistoricalSeriesOut(
            name=name, points=[HistoricalPointOut(bucket=r.b, value=float(r.v)) for r in rows]
        )

    runs_ok = await series(
        "runs_done",
        f"SELECT {bucket_expr.replace('ts', 'started_at')} AS b, count(*) AS v FROM agent_runs"
        f" WHERE started_at >= now() - interval '{interval}' AND status = 'done' {tenant_filter_runs}"
        " GROUP BY b ORDER BY b",
    )
    runs_failed = await series(
        "runs_failed",
        f"SELECT {bucket_expr.replace('ts', 'started_at')} AS b, count(*) AS v FROM agent_runs"
        f" WHERE started_at >= now() - interval '{interval}' AND status = 'failed'"
        f" {tenant_filter_runs} GROUP BY b ORDER BY b",
    )
    accuracy_pct = await series(
        "accuracy_percent",
        f"SELECT {bucket_expr.replace('ts', 'v.created_at')} AS b,"
        " round(count(*) FILTER (WHERE v.verdict = 'correct') * 100.0"
        "   / NULLIF(count(*), 0), 1) AS v"
        " FROM shadow_verdicts v JOIN agent_actions a ON a.id = v.action_id"
        f" WHERE v.created_at >= now() - interval '{interval}'"
        + (" AND a.tenant_id = CAST(:tenant AS uuid)" if tenant_id else "")
        + " GROUP BY b ORDER BY b",
    )
    page_load = await series(
        "avg_page_load_ms",
        f"SELECT {bucket_expr.replace('ts', 'created_at')} AS b, avg(load_ms) AS v"
        f" FROM page_perf_events WHERE created_at >= now() - interval '{interval}'"
        " AND load_ms IS NOT NULL"
        + (" AND tenant_id = CAST(:tenant AS uuid)" if tenant_id else "")
        + " GROUP BY b ORDER BY b",
    )
    api_latency = await series(
        "p95_llm_latency_ms",
        f"SELECT {bucket_expr.replace('ts', 'created_at')} AS b,"
        " percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms) AS v"
        f" FROM llm_calls WHERE created_at >= now() - interval '{interval}' AND outcome = 'ok'"
        + (" AND tenant_id = CAST(:tenant AS uuid)" if tenant_id else "")
        + " GROUP BY b ORDER BY b",
    )
    error_rate = await series(
        "error_rate_percent",
        f"SELECT {bucket_expr.replace('ts', 'started_at')} AS b,"
        " round(count(*) FILTER (WHERE status = 'failed') * 100.0"
        "   / NULLIF(count(*), 0), 1) AS v"
        f" FROM agent_runs WHERE started_at >= now() - interval '{interval}' {tenant_filter_runs}"
        " GROUP BY b ORDER BY b",
    )
    active_users = await series(
        "active_users",
        f"SELECT {bucket_expr.replace('ts', 'occurred_at')} AS b,"
        " count(DISTINCT actor_user_id) AS v FROM audit_log"
        f" WHERE occurred_at >= now() - interval '{interval}' AND actor_user_id IS NOT NULL"
        + (" AND tenant_id = CAST(:tenant AS uuid)" if tenant_id else "")
        + " GROUP BY b ORDER BY b",
    )
    return HistoricalOut(
        range=range_,
        series=[
            runs_ok,
            runs_failed,
            accuracy_pct,
            page_load,
            api_latency,
            error_rate,
            active_users,
        ],
    )
