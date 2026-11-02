"""Real executed tests for the P24 Control Tower additions: agent-wise performance,
client-wise monitoring, provider-wise API monitoring, 90d historical, and the new
alert checks (agent run failure spike, provider API failure rate, alert dedupe /
auto-resolve). These exercise app.monitoring.queries and app.core.alerts directly
against a real Postgres instance (not mocked), plus the admin HTTP routes."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import alerts as core_alerts
from app.core.db import session_scope
from app.monitoring import queries
from tests.auth.helpers import bearer
from tests.auth.test_admin_flow import _admin_login_with_mfa
from tests.rls.test_p4_tables import tenant

Factory = async_sessionmaker[AsyncSession]


async def _make_run(
    factory: Factory,
    tid: uuid.UUID,
    *,
    seq: int,
    graph_version: str = "v0.1",
    template_id: str = "order_status",
    run_status: str = "done",
    verdict: str | None = None,
    reported: bool = False,
    started_minutes_ago: int = 5,
) -> uuid.UUID:
    async with session_scope(factory, tenant_id=tid) as session:
        customer = (
            await session.execute(
                text("INSERT INTO customers (phone_e164) VALUES (:p) RETURNING id"),
                {"p": f"+9198000{seq:05d}"},
            )
        ).scalar_one()
        conv = (
            await session.execute(
                text(
                    "INSERT INTO conversations (customer_id, channel)"
                    " VALUES (:c, 'whatsapp') RETURNING id"
                ),
                {"c": customer},
            )
        ).scalar_one()
        event = (
            await session.execute(
                text(
                    "INSERT INTO inbound_events (channel, external_id, raw_payload)"
                    " VALUES ('whatsapp', :e, '{}') RETURNING id"
                ),
                {"e": f"ct2-{tid.hex[:8]}-{seq}"},
            )
        ).scalar_one()
        started = datetime.now(UTC) - timedelta(minutes=started_minutes_ago)
        run = (
            await session.execute(
                text(
                    "INSERT INTO agent_runs (inbound_event_id, conversation_id, graph_version,"
                    " mode, status, decision, started_at, finished_at)"
                    " VALUES (:e, :c, :g, 'auto', :st,"
                    " jsonb_build_object('reply',"
                    " jsonb_build_object('template_id', CAST(:tpl AS text))),"
                    " :s, :f) RETURNING id"
                ),
                {
                    "e": event,
                    "c": conv,
                    "g": graph_version,
                    "st": run_status,
                    "tpl": template_id,
                    "s": started,
                    "f": started + timedelta(seconds=2) if run_status != "failed" else None,
                },
            )
        ).scalar_one()
        action = (
            await session.execute(
                text(
                    "INSERT INTO agent_actions (run_id, conversation_id, action_type,"
                    " idempotency_key, status, recipient, body_text)"
                    " VALUES (:r, :c, 'reply_customer', :k, 'sent', '+91', 'reply')"
                    " RETURNING id"
                ),
                {"r": run, "c": conv, "k": f"ct2-key-{tid.hex[:8]}-{seq}"},
            )
        ).scalar_one()
        if verdict is not None:
            await session.execute(
                text(
                    "INSERT INTO shadow_verdicts (action_id, verdict, by_user)"
                    " VALUES (:a, :v, :u)"
                ),
                {"a": action, "v": verdict, "u": uuid.uuid4()},
            )
        if reported:
            msg = (
                await session.execute(
                    text(
                        "INSERT INTO messages (conversation_id, direction, sender, channel,"
                        " content) VALUES (:c, 'out', 'agent', 'whatsapp', 'reply') RETURNING id"
                    ),
                    {"c": conv},
                )
            ).scalar_one()
            await session.execute(
                text(
                    "INSERT INTO answer_reports (message_id, action_id, reported_by, reason)"
                    " VALUES (:m, :a, :u, 'galat jawab')"
                ),
                {"m": msg, "a": action, "u": uuid.uuid4()},
            )
        return run


async def test_agent_performance_hides_misleading_accuracy_until_enough_evaluated(
    owner_factory: Factory,
) -> None:
    tid = await tenant(owner_factory)
    for i in range(3):
        await _make_run(owner_factory, tid, seq=i, verdict="correct")
    async with session_scope(owner_factory, tenant_id=tid, super_admin=True) as session:
        out = await queries.agent_performance(
            session, tenant_id=tid, category=None, graph_version=None, date_from=None, date_to=None
        )
    row = next(r for r in out.rows if r.task_category == "order_status")
    assert row.total_evaluated == 3 < out.min_evaluated_for_ranking
    assert row.insufficient_data is True
    assert row.accuracy_percent is None  # never shows a misleading % below the threshold

    for i in range(3, out.min_evaluated_for_ranking + 2):
        await _make_run(owner_factory, tid, seq=i, verdict="correct")
    await _make_run(owner_factory, tid, seq=100, verdict="wrong")
    await _make_run(owner_factory, tid, seq=101)  # unverified: never counted as correct

    async with session_scope(owner_factory, tenant_id=tid, super_admin=True) as session:
        out = await queries.agent_performance(
            session, tenant_id=tid, category=None, graph_version=None, date_from=None, date_to=None
        )
    row = next(r for r in out.rows if r.task_category == "order_status")
    assert row.insufficient_data is False
    assert row.unverified == 1
    assert row.accuracy_percent == round(row.correct * 100 / row.total_evaluated, 1)
    assert row.rank == 1


async def test_agent_performance_handoff_and_human_correction_counts(
    owner_factory: Factory,
) -> None:
    tid = await tenant(owner_factory)
    await _make_run(owner_factory, tid, seq=1, template_id="refund", reported=True)
    await _make_run(owner_factory, tid, seq=2, template_id="refund")
    async with session_scope(owner_factory, tenant_id=tid, super_admin=True) as session:
        out = await queries.agent_performance(
            session,
            tenant_id=tid,
            category="refund",
            graph_version=None,
            date_from=None,
            date_to=None,
        )
    row = out.rows[0]
    assert row.task_category == "refund"
    assert row.human_corrections == 1
    assert row.incorrect == 1  # a reported reply is always 'wrong', even with no verdict


async def test_provider_breakdown_never_shows_false_healthy_for_missing_data(
    owner_factory: Factory,
) -> None:
    tid = await tenant(owner_factory)

    async def calls(provider: str, statuses: list[int]) -> None:
        async with session_scope(owner_factory, tenant_id=tid) as session:
            for s in statuses:
                await session.execute(
                    text(
                        "INSERT INTO external_calls (provider, operation, http_status, latency_ms)"
                        " VALUES (:p, 'op', :s, 120)"
                    ),
                    {"p": provider, "s": s},
                )

    await calls("shopify", [200] * 20)  # 100% -> healthy
    await calls("gupshup", [200] * 8 + [500] * 2)  # 80% -> degraded
    await calls("gmail", [500] * 6 + [200] * 4)  # 40% -> failed
    # plivo and shiprocket deliberately get zero calls -> must stay absent/"unknown"

    async with session_scope(owner_factory, super_admin=True) as session:
        rows = await queries.provider_breakdown(session, tenant_id=tid, window="24h")
    by_provider = {r.provider: r for r in rows}
    assert by_provider["shopify"].health == "healthy"
    assert by_provider["gupshup"].health == "degraded"
    assert by_provider["gmail"].health == "failed"
    assert "plivo" not in by_provider  # never fabricated as "healthy" or any other state


async def test_client_monitoring_isolated_between_tenants_and_picks_worst(
    owner_factory: Factory,
) -> None:
    tid_a = await tenant(owner_factory)
    tid_b = await tenant(owner_factory)
    await _make_run(owner_factory, tid_a, seq=1, template_id="order_status")
    await _make_run(owner_factory, tid_a, seq=2, template_id="order_status", run_status="failed")
    async with session_scope(owner_factory, tenant_id=tid_a) as session:
        await session.execute(
            text(
                "INSERT INTO external_calls (provider, operation, http_status, latency_ms)"
                " VALUES ('shopify', 'op', 500, 100)"
            )
        )
    await _make_run(owner_factory, tid_b, seq=1, template_id="order_status")

    async with session_scope(owner_factory, tenant_id=tid_a, super_admin=True) as session:
        out_a = await queries.client_monitoring(session, tid_a, window="24h")
    async with session_scope(owner_factory, tenant_id=tid_b, super_admin=True) as session:
        out_b = await queries.client_monitoring(session, tid_b, window="24h")
    assert out_a is not None and out_b is not None
    assert out_a.runs_total == 2 and out_a.runs_failed == 1
    assert out_b.runs_total == 1 and out_b.runs_failed == 0  # tenant B unaffected by A's data
    assert out_a.worst_provider == "shopify"

    async with session_scope(owner_factory, super_admin=True) as session:
        missing = await queries.client_monitoring(session, uuid.uuid4(), window="24h")
    assert missing is None


async def test_historical_90d_includes_new_series(owner_factory: Factory) -> None:
    async with session_scope(owner_factory, super_admin=True) as session:
        out = await queries.historical(session, "90d", None)
    assert out.range == "90d"
    names = {s.name for s in out.series}
    assert {"integration_error_rate_percent", "alerts_opened"} <= names


async def test_alert_agent_run_failure_spike_dedupe_and_autoresolve(
    owner_factory: Factory,
) -> None:
    tid = await tenant(owner_factory)
    for i in range(6):
        await _make_run(owner_factory, tid, seq=i, run_status="failed", started_minutes_ago=2)

    async with session_scope(owner_factory, super_admin=True) as session:
        await core_alerts.scan_and_upsert(session)
        rows = (
            await session.execute(
                text(
                    "SELECT severity, status, service, error_id FROM alerts"
                    " WHERE kind = 'agent_run_failure_spike' AND tenant_id = :t"
                ),
                {"t": tid},
            )
        ).all()
    assert len(rows) == 1
    assert rows[0].severity == "critical"
    assert rows[0].status == "open"
    assert rows[0].service == "agent"

    # Scanning again must not create a second row for the same breach (dedupe_key).
    async with session_scope(owner_factory, super_admin=True) as session:
        await core_alerts.scan_and_upsert(session)
        again = (
            await session.execute(
                text(
                    "SELECT count(*) AS n FROM alerts"
                    " WHERE kind = 'agent_run_failure_spike' AND tenant_id = :t"
                ),
                {"t": tid},
            )
        ).scalar_one()
    assert again == 1

    # Once the failures age out of the 15-minute window, the alert auto-resolves.
    async with session_scope(owner_factory, super_admin=True) as session:
        await session.execute(
            text(
                "UPDATE agent_runs SET started_at = now() - interval '1 hour'"
                " WHERE tenant_id = :t"
            ),
            {"t": tid},
        )
        await core_alerts.scan_and_upsert(session)
        status = (
            await session.execute(
                text(
                    "SELECT status FROM alerts"
                    " WHERE kind = 'agent_run_failure_spike' AND tenant_id = :t"
                ),
                {"t": tid},
            )
        ).scalar_one()
    assert status == "resolved"


async def test_alert_provider_api_failure_rate_severity_by_magnitude(
    owner_factory: Factory,
) -> None:
    tid = await tenant(owner_factory)
    async with session_scope(owner_factory, tenant_id=tid) as session:
        for _ in range(8):
            await session.execute(
                text(
                    "INSERT INTO external_calls (provider, operation, http_status, latency_ms,"
                    " created_at) VALUES ('gupshup', 'send', 500, 100, now())"
                )
            )
        for _ in range(2):
            await session.execute(
                text(
                    "INSERT INTO external_calls (provider, operation, http_status, latency_ms,"
                    " created_at) VALUES ('gupshup', 'send', 200, 100, now())"
                )
            )
    async with session_scope(owner_factory, super_admin=True) as session:
        await core_alerts.scan_and_upsert(session)
        row = (
            await session.execute(
                text(
                    "SELECT severity, service FROM alerts"
                    " WHERE kind = 'provider_api_failure' AND tenant_id = :t"
                ),
                {"t": tid},
            )
        ).one()
    assert row.severity == "critical"  # 80% failing >= 70% -> critical, not just "high"
    assert row.service == "gupshup"


async def test_admin_agent_performance_and_client_endpoints_require_admin_and_work(
    client, runtime_factory: Factory  # noqa: ANN001
) -> None:
    anon = await client.get("/admin/monitoring/agent-performance")
    assert anon.status_code == 401

    _, verified, _ = await _admin_login_with_mfa(client, runtime_factory)
    tid = await tenant(runtime_factory)
    await _make_run(runtime_factory, tid, seq=1, template_id="order_status")

    perf = await client.get("/admin/monitoring/agent-performance", headers=bearer(verified))
    assert perf.status_code == 200
    assert any(r["task_category"] == "order_status" for r in perf.json()["rows"])

    detail = await client.get(f"/admin/monitoring/clients/{tid}", headers=bearer(verified))
    assert detail.status_code == 200
    assert detail.json()["tenant_id"] == str(tid)

    missing = await client.get(
        f"/admin/monitoring/clients/{uuid.uuid4()}", headers=bearer(verified)
    )
    assert missing.status_code == 404
