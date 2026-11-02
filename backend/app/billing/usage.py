"""Enterprise usage-metering + billing-intelligence layer, built on top of the existing
billing module. Reuse-first, per DECISIONS.md: LLM usage/cost reuses service._LLM_USAGE +
service.price_llm (already correct for any date range, not just a calendar month); Cloud
Run compute reuses service._COMPUTE_USAGE + service.price_cloudrun; voice/telephony reuses
service._VOICE_USAGE. Only GCP app-level usage, Plivo CDR, speech usage and messages needed
new range queries (the existing ones in service.py filter by an exact `billing_period` month,
not an arbitrary date range).

Honesty rules carried over from the rest of billing/: never show a cost as zero when it is
actually "no rate configured" (flag it instead), never claim a BigQuery "Connected" status
that billing_settings does not actually have, and always say plainly when a number is an
estimate/allocation rather than a provider-billed actual.
"""

import csv
import io
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import calc
from app.billing import service as billing_service
from app.billing.usage_schemas import (
    AgentPerformanceOut,
    BillingExportSyncStatusOut,
    ClientBillingOut,
    ClientBillingSummaryOut,
    ClientFinancialSummaryOut,
    ClientInfoOut,
    ClientUsageOut,
    CostBreakdownOut,
    DatePreset,
    GcpUsageLineOut,
    LlmUsageLineOut,
    MessageUsageOut,
    OtherServiceCostOut,
    OverviewOut,
    RangeOut,
    ReportRowOut,
    ReportType,
    UsageLedgerFilterIn,
    UsageLedgerOut,
    UsageLedgerRowOut,
    UsageLimitIn,
    UsageLimitOut,
    UsageLimitsOut,
    UsageLimitStatusesOut,
    UsageLimitStatusOut,
)
from app.core import timeutil
from app.core.errors import AppError

_ZERO = Decimal(0)
_HUNDRED = Decimal(100)
_PAISE = Decimal(100)


def _money(paise_or_rupees: Decimal | int, *, is_paise: bool = True) -> Decimal:
    value = Decimal(paise_or_rupees)
    return (value / _PAISE) if is_paise else value


def _pct(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator == 0:
        return _ZERO
    return (numerator * _HUNDRED / denominator).quantize(Decimal("0.01"))


# ---------------------------------------------------------------- date range resolution
def resolve_range(preset: DatePreset, start: date | None, end: date | None) -> tuple[date, date]:
    today = timeutil.to_ist(timeutil.utc_now()).date()
    if preset == "today":
        return today, today
    if preset == "this_week":
        monday = today - timedelta(days=today.weekday())
        return monday, today
    if preset == "this_month":
        return today.replace(day=1), today
    if preset == "previous_month":
        first_this = today.replace(day=1)
        last_of_prev = first_this - timedelta(days=1)
        return last_of_prev.replace(day=1), last_of_prev
    if start is None or end is None:
        raise AppError("E-VAL-001", detail="custom range needs start and end")
    if end < start:
        raise AppError("E-VAL-001", detail="end must not be before start")
    return start, end


def _bounds(start: date, end: date) -> tuple[datetime, datetime]:
    return (
        datetime(start.year, start.month, start.day, tzinfo=timeutil.IST),
        datetime(end.year, end.month, end.day, tzinfo=timeutil.IST) + timedelta(days=1),
    )


async def _fx_rate(session: AsyncSession, end: date) -> Decimal | None:
    row = (
        await session.execute(
            text(
                "SELECT usd_inr FROM billing_periods WHERE period_start <= :e"
                " AND usd_inr IS NOT NULL ORDER BY period_start DESC LIMIT 1"
            ),
            {"e": end},
        )
    ).scalar_one_or_none()
    return Decimal(row) if row is not None else None


# ---------------------------------------------------------------- range queries (new)
_MESSAGES_RANGE = text(
    "SELECT tenant_id, direction, count(*) AS n FROM messages"
    " WHERE created_at >= :s AND created_at < :e"
    " AND (CAST(:t AS uuid) IS NULL OR tenant_id = CAST(:t AS uuid))"
    " GROUP BY tenant_id, direction"
)
_AGENT_RUNS_RANGE = text(
    "SELECT tenant_id, status, count(*) AS n,"
    " COALESCE(sum(extract(epoch FROM (finished_at - started_at)))"
    " FILTER (WHERE finished_at IS NOT NULL), 0) AS secs"
    " FROM agent_runs WHERE started_at >= :s AND started_at < :e"
    " AND (CAST(:t AS uuid) IS NULL OR tenant_id = CAST(:t AS uuid))"
    " GROUP BY tenant_id, status"
)
_RETRY_RANGE = text(
    "SELECT tenant_id, COALESCE(sum(GREATEST(attempts - 1, 0)), 0) AS retries"
    " FROM agent_actions WHERE created_at >= :s AND created_at < :e"
    " AND (CAST(:t AS uuid) IS NULL OR tenant_id = CAST(:t AS uuid))"
    " GROUP BY tenant_id"
)
_GCP_RANGE = text(
    "SELECT tenant_id, category, COALESCE(sum(quantity), 0) AS qty,"
    " COALESCE(max(unit), '') AS unit, COALESCE(sum(cost_usd), 0) AS cost_usd"
    " FROM gcp_usage_events WHERE created_at >= :s AND created_at < :e"
    " AND (CAST(:t AS uuid) IS NULL OR tenant_id = CAST(:t AS uuid))"
    " GROUP BY tenant_id, category"
)
_PLIVO_RANGE = text(
    "SELECT tenant_id, direction, count(*) AS calls,"
    " COALESCE(sum(billed_seconds), 0) AS secs, COALESCE(sum(computed_cost_paise), 0) AS cost"
    " FROM plivo_call_records WHERE created_at >= :s AND created_at < :e"
    " AND (CAST(:t AS uuid) IS NULL OR tenant_id = CAST(:t AS uuid))"
    " GROUP BY tenant_id, direction"
)
_PLIVO_RENTAL_RANGE = text(
    "SELECT DISTINCT ON (plivo_number) tenant_id, monthly_rate_paise FROM"
    " plivo_number_assignments WHERE assigned_from <= :e"
    " AND (assigned_to IS NULL OR assigned_to >= :s)"
    " ORDER BY plivo_number, assigned_from DESC"
)
_SPEECH_RANGE = text(
    "SELECT tenant_id, provider, COALESCE(sum(quantity), 0) AS qty,"
    " COALESCE(sum(cost_paise), 0) AS cost FROM speech_usage_records"
    " WHERE created_at >= :s AND created_at < :e"
    " AND (CAST(:t AS uuid) IS NULL OR tenant_id = CAST(:t AS uuid))"
    " GROUP BY tenant_id, provider"
)
_INVOICE_REVENUE_RANGE = text(
    "SELECT tenant_id, COALESCE(sum(total_paise), 0) AS rev FROM invoices"
    " WHERE status = 'final' AND cancelled_at IS NULL"
    " AND invoice_date >= :s AND invoice_date <= :e"
    " AND (CAST(:t AS uuid) IS NULL OR tenant_id = CAST(:t AS uuid))"
    " GROUP BY tenant_id"
)
_TENANTS_ALL = text(
    "SELECT t.id, t.name, t.slug, COALESCE(p.billing_status, 'enabled') AS billing_status"
    " FROM tenants t LEFT JOIN tenant_billing_profiles p ON p.tenant_id = t.id"
    " WHERE (CAST(:t AS uuid) IS NULL OR t.id = CAST(:t AS uuid)) ORDER BY t.name"
)
_UNPRICED_RANGE = text(
    "SELECT DISTINCT c.provider, c.model FROM llm_calls c"
    " WHERE c.created_at >= :s AND c.created_at < :e"
    " AND (c.input_tokens > 0 OR c.output_tokens > 0)"
    " AND NOT EXISTS (SELECT 1 FROM billing_llm_rates r"
    " WHERE r.provider = c.provider AND r.model = c.model) ORDER BY c.provider, c.model"
)
_LATEST_RECON_IN_RANGE = text(
    "SELECT COALESCE(sum(unallocated_usd), 0) FROM gcp_reconciliation_runs"
    " WHERE created_at >= :s AND created_at < :e"
)


@dataclass(frozen=True)
class _Totals:
    llm_lines_by_tenant: dict[uuid.UUID, list[billing_service.LlmLine]]
    compute_by_tenant: dict[uuid.UUID, billing_service.Compute]
    voice_by_tenant: dict[uuid.UUID, billing_service.VoiceUsage]
    gcp_rows: list[Any]
    plivo_rows: list[Any]
    plivo_rental_by_tenant: dict[uuid.UUID, int]
    speech_rows: list[Any]
    messages_rows: list[Any]
    agent_rows: list[Any]
    retry_by_tenant: dict[uuid.UUID, int]
    revenue_by_tenant: dict[uuid.UUID, int]
    fx: Decimal | None
    settings: Any


async def _collect_range(
    session: AsyncSession, start: date, end: date, tenant_id: uuid.UUID | None
) -> _Totals:
    s, e = _bounds(start, end)
    t = str(tenant_id) if tenant_id else None
    settings = await billing_service.load_settings(session)
    fx = await _fx_rate(session, end)
    rate_rows = (await session.execute(billing_service._RATES)).all()  # noqa: SLF001
    rates: billing_service.Rates = {
        (r.provider, r.model): (r.input_usd_per_million, r.output_usd_per_million)
        for r in rate_rows
    }
    llm_rows = (
        await session.execute(billing_service._LLM_USAGE, {"s": s, "e": e, "t": t})  # noqa: SLF001
    ).all()
    llm_by_tenant: dict[uuid.UUID, list[billing_service.LlmUsage]] = {}
    for r in llm_rows:
        llm_by_tenant.setdefault(r.tenant_id, []).append(
            billing_service.LlmUsage(
                r.provider,
                r.model,
                int(r.calls),
                int(r.input_tokens),
                int(r.output_tokens),
                int(r.est_cost_micro),
                int(r.unpriced_calls),
            )
        )
    llm_lines_by_tenant = {
        tid: billing_service.price_llm(usages, rates, fx) for tid, usages in llm_by_tenant.items()
    }
    compute_rows = (
        await session.execute(billing_service._COMPUTE_USAGE, {"s": s, "e": e})  # noqa: SLF001
    ).all()
    compute_by_tenant = {
        r.tenant_id: billing_service.Compute(int(r.ms), int(r.jobs))
        for r in compute_rows
        if tenant_id is None or r.tenant_id == tenant_id
    }
    voice_rows = (
        await session.execute(billing_service._VOICE_USAGE, {"s": s, "e": e})  # noqa: SLF001
    ).all()
    voice_by_tenant = {
        r.tenant_id: billing_service.VoiceUsage(int(r.seconds), int(r.calls))
        for r in voice_rows
        if tenant_id is None or r.tenant_id == tenant_id
    }
    gcp_rows = (await session.execute(_GCP_RANGE, {"s": s, "e": e, "t": t})).all()
    plivo_rows = (await session.execute(_PLIVO_RANGE, {"s": s, "e": e, "t": t})).all()
    rental_rows = (await session.execute(_PLIVO_RENTAL_RANGE, {"s": start, "e": end})).all()
    plivo_rental_by_tenant: dict[uuid.UUID, int] = {}
    for r in rental_rows:
        if tenant_id is None or r.tenant_id == tenant_id:
            plivo_rental_by_tenant[r.tenant_id] = plivo_rental_by_tenant.get(r.tenant_id, 0) + int(
                r.monthly_rate_paise
            )
    speech_rows = (await session.execute(_SPEECH_RANGE, {"s": s, "e": e, "t": t})).all()
    messages_rows = (await session.execute(_MESSAGES_RANGE, {"s": s, "e": e, "t": t})).all()
    agent_rows = (await session.execute(_AGENT_RUNS_RANGE, {"s": s, "e": e, "t": t})).all()
    retry_rows = (await session.execute(_RETRY_RANGE, {"s": s, "e": e, "t": t})).all()
    retry_by_tenant = {r.tenant_id: int(r.retries) for r in retry_rows}
    revenue_rows = (
        await session.execute(_INVOICE_REVENUE_RANGE, {"s": start, "e": end, "t": t})
    ).all()
    revenue_by_tenant = {r.tenant_id: int(r.rev) for r in revenue_rows}
    return _Totals(
        llm_lines_by_tenant=llm_lines_by_tenant,
        compute_by_tenant=compute_by_tenant,
        voice_by_tenant=voice_by_tenant,
        gcp_rows=list(gcp_rows),
        plivo_rows=list(plivo_rows),
        plivo_rental_by_tenant=plivo_rental_by_tenant,
        speech_rows=list(speech_rows),
        messages_rows=list(messages_rows),
        agent_rows=list(agent_rows),
        retry_by_tenant=retry_by_tenant,
        revenue_by_tenant=revenue_by_tenant,
        fx=fx,
        settings=settings,
    )


def _llm_cost_inr_for_tenant(totals: _Totals, tenant_id: uuid.UUID) -> Decimal:
    lines = totals.llm_lines_by_tenant.get(tenant_id, [])
    return _money(sum(line.amount_paise for line in lines))


def _cloudrun_cost_inr_for_tenant(totals: _Totals, tenant_id: uuid.UUID) -> Decimal:
    compute = totals.compute_by_tenant.get(tenant_id)
    if compute is None or totals.fx is None:
        return _ZERO
    priced = billing_service.price_cloudrun(totals.settings, totals.fx, None, {tenant_id: compute})
    return _money(priced[tenant_id].amount_paise)


def _speech_cost_inr_for_tenant(totals: _Totals, tenant_id: uuid.UUID) -> Decimal:
    total = sum(int(r.cost) for r in totals.speech_rows if r.tenant_id == tenant_id)
    return _money(total)


def _plivo_cost_inr_for_tenant(totals: _Totals, tenant_id: uuid.UUID) -> Decimal:
    calls = sum(int(r.cost) for r in totals.plivo_rows if r.tenant_id == tenant_id)
    rental = totals.plivo_rental_by_tenant.get(tenant_id, 0)
    return _money(calls + rental)


def _gcp_cost_inr_for_tenant(totals: _Totals, tenant_id: uuid.UUID) -> Decimal:
    if totals.fx is None:
        return _ZERO
    total_usd = sum(
        (Decimal(r.cost_usd) for r in totals.gcp_rows if r.tenant_id == tenant_id), Decimal(0)
    )
    return _money(calc.usd_to_paise(total_usd, totals.fx))


def _messages_for_tenant(totals: _Totals, tenant_id: uuid.UUID | None) -> MessageUsageOut:
    inc = out = 0
    for r in totals.messages_rows:
        if tenant_id is not None and r.tenant_id != tenant_id:
            continue
        if r.direction == "in":
            inc += int(r.n)
        else:
            out += int(r.n)
    return MessageUsageOut(incoming=inc, outgoing=out, total=inc + out)


def _agent_performance_for_tenant(
    totals: _Totals, tenant_id: uuid.UUID | None, llm_cost_inr: Decimal
) -> AgentPerformanceOut:
    completed = failed = pending = skipped = 0
    exec_seconds = _ZERO
    for r in totals.agent_rows:
        if tenant_id is not None and r.tenant_id != tenant_id:
            continue
        n = int(r.n)
        if r.status == "done":
            completed += n
        elif r.status == "failed":
            failed += n
        elif r.status == "running":
            pending += n
        else:
            skipped += n
        exec_seconds += Decimal(r.secs)
    total = completed + failed + pending + skipped
    retries = (
        totals.retry_by_tenant.get(tenant_id, 0)
        if tenant_id is not None
        else sum(totals.retry_by_tenant.values())
    )
    avg_seconds = (exec_seconds / total).quantize(Decimal("0.01")) if total else _ZERO
    cost_per_task = (llm_cost_inr / completed).quantize(Decimal("0.0001")) if completed else _ZERO
    return AgentPerformanceOut(
        total_tasks=total,
        completed=completed,
        failed=failed,
        pending=pending,
        success_rate_percent=_pct(Decimal(completed), Decimal(total)),
        avg_execution_seconds=avg_seconds,
        total_execution_seconds=exec_seconds.quantize(Decimal("0.01")),
        retry_count=retries,
        cost_per_successful_task_inr=cost_per_task,
    )


# ---------------------------------------------------------------- A. Billing Overview
async def overview(
    session: AsyncSession, preset: DatePreset, start: date | None, end: date | None
) -> OverviewOut:
    range_start, range_end = resolve_range(preset, start, end)
    totals = await _collect_range(session, range_start, range_end, None)
    tenants = {r.tenant_id for r in totals.messages_rows} | set(totals.llm_lines_by_tenant)
    tenants |= {r.tenant_id for r in totals.agent_rows}
    tenants |= {r.tenant_id for r in totals.gcp_rows if r.tenant_id is not None}
    tenants |= {r.tenant_id for r in totals.plivo_rows if r.tenant_id is not None}
    tenants |= {r.tenant_id for r in totals.speech_rows}
    tenants |= set(totals.revenue_by_tenant)

    llm_cost = sum((_llm_cost_inr_for_tenant(totals, t) for t in tenants), _ZERO)
    cloudrun_cost = sum((_cloudrun_cost_inr_for_tenant(totals, t) for t in tenants), _ZERO)
    speech_cost = sum((_speech_cost_inr_for_tenant(totals, t) for t in tenants), _ZERO)
    plivo_cost = sum((_plivo_cost_inr_for_tenant(totals, t) for t in tenants), _ZERO)
    gcp_cost = sum((_gcp_cost_inr_for_tenant(totals, t) for t in tenants), _ZERO)
    total_cost = llm_cost + cloudrun_cost + speech_cost + plivo_cost + gcp_cost
    revenue = _money(sum(totals.revenue_by_tenant.values()))
    profit = revenue - total_cost
    unalloc_usd = (
        await session.execute(
            _LATEST_RECON_IN_RANGE,
            {"s": _bounds(range_start, range_end)[0], "e": _bounds(range_start, range_end)[1]},
        )
    ).scalar_one()
    unalloc_inr = _money(calc.usd_to_paise(Decimal(unalloc_usd), totals.fx)) if totals.fx else _ZERO
    unpriced = (
        await session.execute(
            _UNPRICED_RANGE,
            {"s": _bounds(range_start, range_end)[0], "e": _bounds(range_start, range_end)[1]},
        )
    ).all()
    note = OverviewOut.model_fields["note"].default
    if totals.fx is None:
        note += (
            " No usd_inr FX rate is set for this range yet (Billing Settings > Period), so"
            " USD-denominated costs (Cloud Run, other GCP) show as 0 here, not fabricated."
        )
    return OverviewOut(
        range=RangeOut(preset=preset, start=range_start, end=range_end),
        total_revenue_inr=revenue,
        total_client_usage_cost_inr=total_cost,
        costs=CostBreakdownOut(
            llm_cost_inr=llm_cost,
            cloudrun_cost_inr=cloudrun_cost,
            speech_cost_inr=speech_cost,
            plivo_cost_inr=plivo_cost,
            other_gcp_cost_inr=gcp_cost,
            total_operating_cost_inr=total_cost,
        ),
        gross_profit_inr=profit,
        profit_margin_percent=_pct(profit, revenue),
        unallocated_infra_cost_inr=unalloc_inr,
        active_clients=len(tenants),
        unpriced_llm_models=[f"{r.provider}/{r.model}" for r in unpriced],
        note=note,
    )


# ---------------------------------------------------------------- B. Client-wise billing
def _financial_summary(
    totals: _Totals, tenant_id: uuid.UUID
) -> tuple[ClientFinancialSummaryOut, Decimal]:
    llm_cost = _llm_cost_inr_for_tenant(totals, tenant_id)
    cloud_cost = _cloudrun_cost_inr_for_tenant(totals, tenant_id) + _gcp_cost_inr_for_tenant(
        totals, tenant_id
    )
    calling_cost = _plivo_cost_inr_for_tenant(totals, tenant_id)
    other_cost = _speech_cost_inr_for_tenant(totals, tenant_id)
    total_cost = llm_cost + cloud_cost + calling_cost + other_cost
    revenue = _money(totals.revenue_by_tenant.get(tenant_id, 0))
    profit = revenue - total_cost
    return (
        ClientFinancialSummaryOut(
            total_llm_cost_inr=llm_cost,
            total_cloud_cost_inr=cloud_cost,
            total_calling_cost_inr=calling_cost,
            total_other_cost_inr=other_cost,
            total_operating_cost_inr=total_cost,
            client_revenue_inr=revenue,
            gross_profit_inr=profit,
            profit_margin_percent=_pct(profit, revenue),
        ),
        total_cost,
    )


async def list_clients(
    session: AsyncSession, preset: DatePreset, start: date | None, end: date | None
) -> list[ClientBillingSummaryOut]:
    range_start, range_end = resolve_range(preset, start, end)
    totals = await _collect_range(session, range_start, range_end, None)
    tenant_rows = (await session.execute(_TENANTS_ALL, {"t": None})).all()
    out: list[ClientBillingSummaryOut] = []
    for t in tenant_rows:
        summary, _ = _financial_summary(totals, t.id)
        messages = _messages_for_tenant(totals, t.id)
        out.append(
            ClientBillingSummaryOut(
                tenant_id=t.id,
                tenant_name=t.name,
                slug=t.slug,
                billing_status=t.billing_status,
                messages_total=messages.total,
                total_operating_cost_inr=summary.total_operating_cost_inr,
                client_revenue_inr=summary.client_revenue_inr,
                gross_profit_inr=summary.gross_profit_inr,
                profit_margin_percent=summary.profit_margin_percent,
            )
        )
    return out


async def client_detail(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    preset: DatePreset,
    start: date | None,
    end: date | None,
) -> ClientBillingOut:
    range_start, range_end = resolve_range(preset, start, end)
    totals = await _collect_range(session, range_start, range_end, tenant_id)
    tenant_rows = (await session.execute(_TENANTS_ALL, {"t": str(tenant_id)})).all()
    if not tenant_rows:
        raise AppError("E-NF-001", detail="tenant not found")
    t = tenant_rows[0]
    messages = _messages_for_tenant(totals, tenant_id)
    llm_cost = _llm_cost_inr_for_tenant(totals, tenant_id)
    agent_perf = _agent_performance_for_tenant(totals, tenant_id, llm_cost)
    llm_lines = []
    for line in totals.llm_lines_by_tenant.get(tenant_id, []):
        total_cost_inr = _money(line.amount_paise)
        if line.rate_in is not None and line.rate_out is not None and totals.fx is not None:
            input_usd = Decimal(line.input_tokens) * line.rate_in / Decimal(1_000_000)
            output_usd = Decimal(line.output_tokens) * line.rate_out / Decimal(1_000_000)
            input_cost_inr = _money(calc.usd_to_paise(input_usd, totals.fx))
            output_cost_inr = _money(calc.usd_to_paise(output_usd, totals.fx))
        else:
            total_tokens = line.input_tokens + line.output_tokens
            input_cost_inr = (
                (total_cost_inr * line.input_tokens / total_tokens) if total_tokens else _ZERO
            )
            output_cost_inr = total_cost_inr - input_cost_inr if total_tokens else _ZERO
        llm_lines.append(
            LlmUsageLineOut(
                provider=line.provider,
                model=line.model,
                api_calls=line.calls,
                input_tokens=line.input_tokens,
                output_tokens=line.output_tokens,
                total_tokens=line.input_tokens + line.output_tokens,
                input_cost_inr=input_cost_inr,
                output_cost_inr=output_cost_inr,
                total_cost_inr=total_cost_inr,
                priced=line.source in ("rate", "call_log"),
            )
        )
    gcp_lines = [
        GcpUsageLineOut(
            category=r.category,
            quantity=Decimal(r.qty),
            unit=r.unit,
            cost_usd=Decimal(r.cost_usd),
            cost_inr=(
                _money(calc.usd_to_paise(Decimal(r.cost_usd), totals.fx)) if totals.fx else _ZERO
            ),
        )
        for r in totals.gcp_rows
        if r.tenant_id == tenant_id
    ]
    plivo_out = sum(
        int(r.cost)
        for r in totals.plivo_rows
        if r.tenant_id == tenant_id and r.direction == "outbound"
    )
    plivo_in = sum(
        int(r.cost)
        for r in totals.plivo_rows
        if r.tenant_id == tenant_id and r.direction == "inbound"
    )
    speech_by_provider = {
        r.provider: int(r.cost) for r in totals.speech_rows if r.tenant_id == tenant_id
    }
    other_costs = OtherServiceCostOut(
        plivo_outbound_inr=_money(plivo_out),
        plivo_inbound_inr=_money(plivo_in),
        plivo_number_rental_inr=_money(totals.plivo_rental_by_tenant.get(tenant_id, 0)),
        stt_cost_inr=_money(
            speech_by_provider.get("sarvam_stt", 0) + speech_by_provider.get("google_stt", 0)
        ),
        tts_cost_inr=_money(
            speech_by_provider.get("sarvam_tts", 0) + speech_by_provider.get("google_tts", 0)
        ),
        other_third_party_inr=_ZERO,
    )
    financial_summary, _ = _financial_summary(totals, tenant_id)
    return ClientBillingOut(
        range=RangeOut(preset=preset, start=range_start, end=range_end),
        client=ClientInfoOut(
            tenant_id=t.id,
            tenant_name=t.name,
            slug=t.slug,
            active_brands=1,  # this platform models one brand per tenant; see DECISIONS.md
            plan=t.billing_status,
            billing_status=t.billing_status,
            billing_period=f"{range_start.isoformat()} to {range_end.isoformat()}",
        ),
        messages=messages,
        agent_performance=agent_perf,
        llm_usage=llm_lines,
        gcp_usage=gcp_lines,
        other_costs=other_costs,
        financial_summary=financial_summary,
    )


# ---------------------------------------------------------------- C. Usage Ledger
async def usage_ledger(session: AsyncSession, body: UsageLedgerFilterIn) -> UsageLedgerOut:
    s, e = _bounds(body.start, body.end)
    t = str(body.tenant_id) if body.tenant_id else None
    fx = await _fx_rate(session, body.end)
    rows: list[UsageLedgerRowOut] = []
    tenant_names = {r.id: r.name for r in (await session.execute(_TENANTS_ALL, {"t": None})).all()}

    def _name(tid: uuid.UUID | None) -> str:
        return tenant_names.get(tid, "shared / unattributed") if tid else "shared / unattributed"

    if body.event_type in (None, "message"):
        for r in (await session.execute(_MESSAGES_RANGE, {"s": s, "e": e, "t": t})).all():
            rows.append(
                UsageLedgerRowOut(
                    event_id=uuid.uuid5(
                        uuid.NAMESPACE_URL, f"message:{r.tenant_id}:{r.direction}:{s}"
                    ),
                    tenant_id=r.tenant_id,
                    tenant_name=_name(r.tenant_id),
                    event_type="message",
                    service_provider="internal",
                    service_name=f"message_{r.direction}",
                    resource_name="messages",
                    quantity=Decimal(r.n),
                    unit="message",
                    started_at=s,
                    ended_at=e,
                    duration_seconds=None,
                    actual_cost_inr=None,
                    estimated_cost_inr=None,
                    status="recorded",
                    created_at=s,
                )
            )
    if body.event_type in (None, "llm_call"):
        llm_rows = (
            await session.execute(billing_service._LLM_USAGE, {"s": s, "e": e, "t": t})  # noqa: SLF001
        ).all()
        for r in llm_rows:
            if body.service_provider and r.provider != body.service_provider:
                continue
            if body.service_name and r.model != body.service_name:
                continue
            rows.append(
                UsageLedgerRowOut(
                    event_id=uuid.uuid5(
                        uuid.NAMESPACE_URL, f"llm:{r.tenant_id}:{r.provider}:{r.model}:{s}"
                    ),
                    tenant_id=r.tenant_id,
                    tenant_name=_name(r.tenant_id),
                    event_type="llm_call",
                    service_provider=r.provider,
                    service_name=r.model,
                    resource_name=f"{int(r.input_tokens)} in / {int(r.output_tokens)} out tokens",
                    quantity=Decimal(r.calls),
                    unit="calls",
                    started_at=s,
                    ended_at=e,
                    duration_seconds=None,
                    actual_cost_inr=(
                        _money(
                            calc.usd_to_paise(Decimal(r.est_cost_micro) / Decimal(1_000_000), fx)
                        )
                        if r.est_cost_micro and fx
                        else None
                    ),
                    estimated_cost_inr=None,
                    status="unpriced" if int(r.unpriced_calls) > 0 else "priced",
                    created_at=s,
                )
            )
    if body.event_type in (None, "gcp_usage"):
        for r in (await session.execute(_GCP_RANGE, {"s": s, "e": e, "t": t})).all():
            if body.service_name and r.category != body.service_name:
                continue
            rows.append(
                UsageLedgerRowOut(
                    event_id=uuid.uuid5(uuid.NAMESPACE_URL, f"gcp:{r.tenant_id}:{r.category}:{s}"),
                    tenant_id=r.tenant_id,
                    tenant_name=_name(r.tenant_id),
                    event_type="gcp_usage",
                    service_provider="google_cloud",
                    service_name=r.category,
                    resource_name=r.category,
                    quantity=Decimal(r.qty),
                    unit=r.unit,
                    started_at=s,
                    ended_at=e,
                    duration_seconds=None,
                    actual_cost_inr=None,
                    estimated_cost_inr=(
                        _money(calc.usd_to_paise(Decimal(r.cost_usd), fx)) if fx else None
                    ),
                    status="measured",
                    created_at=s,
                )
            )
    if body.event_type in (None, "plivo_call"):
        plivo_rows = (
            await session.execute(
                text(
                    "SELECT id, tenant_id, direction, call_status, billed_seconds,"
                    " computed_cost_paise, created_at FROM plivo_call_records"
                    " WHERE created_at >= :s AND created_at < :e"
                    " AND (CAST(:t AS uuid) IS NULL OR tenant_id = CAST(:t AS uuid))"
                    " ORDER BY created_at DESC LIMIT :lim"
                ),
                {"s": s, "e": e, "t": t, "lim": body.limit},
            )
        ).all()
        for r in plivo_rows:
            rows.append(
                UsageLedgerRowOut(
                    event_id=r.id,
                    tenant_id=r.tenant_id,
                    tenant_name=_name(r.tenant_id),
                    event_type="plivo_call",
                    service_provider="plivo",
                    service_name=r.direction,
                    resource_name="voice call",
                    quantity=Decimal(r.billed_seconds),
                    unit="seconds",
                    started_at=r.created_at,
                    ended_at=None,
                    duration_seconds=Decimal(r.billed_seconds),
                    actual_cost_inr=_money(r.computed_cost_paise),
                    estimated_cost_inr=None,
                    status=r.call_status,
                    created_at=r.created_at,
                )
            )
    if body.event_type in (None, "speech_usage"):
        speech_rows = (
            await session.execute(
                text(
                    "SELECT id, tenant_id, provider, quantity, unit, cost_paise, created_at"
                    " FROM speech_usage_records WHERE created_at >= :s AND created_at < :e"
                    " AND (CAST(:t AS uuid) IS NULL OR tenant_id = CAST(:t AS uuid))"
                    " ORDER BY created_at DESC LIMIT :lim"
                ),
                {"s": s, "e": e, "t": t, "lim": body.limit},
            )
        ).all()
        for r in speech_rows:
            rows.append(
                UsageLedgerRowOut(
                    event_id=r.id,
                    tenant_id=r.tenant_id,
                    tenant_name=_name(r.tenant_id),
                    event_type="speech_usage",
                    service_provider=r.provider,
                    service_name=r.provider,
                    resource_name="stt_tts",
                    quantity=Decimal(r.quantity),
                    unit=r.unit,
                    started_at=r.created_at,
                    ended_at=None,
                    duration_seconds=None,
                    actual_cost_inr=_money(r.cost_paise),
                    estimated_cost_inr=None,
                    status="recorded",
                    created_at=r.created_at,
                )
            )
    if body.event_type in (None, "agent_execution"):
        agent_rows = (
            await session.execute(
                text(
                    "SELECT id, tenant_id, status, graph_version, started_at, finished_at"
                    " FROM agent_runs WHERE started_at >= :s AND started_at < :e"
                    " AND (CAST(:t AS uuid) IS NULL OR tenant_id = CAST(:t AS uuid))"
                    " ORDER BY started_at DESC LIMIT :lim"
                ),
                {"s": s, "e": e, "t": t, "lim": body.limit},
            )
        ).all()
        for r in agent_rows:
            duration = (
                Decimal((r.finished_at - r.started_at).total_seconds()) if r.finished_at else None
            )
            rows.append(
                UsageLedgerRowOut(
                    event_id=r.id,
                    tenant_id=r.tenant_id,
                    tenant_name=_name(r.tenant_id),
                    event_type="agent_execution",
                    service_provider="support_agent",
                    service_name=r.graph_version,
                    resource_name="agent_run",
                    quantity=Decimal(1),
                    unit="run",
                    started_at=r.started_at,
                    ended_at=r.finished_at,
                    duration_seconds=duration,
                    actual_cost_inr=None,
                    estimated_cost_inr=None,
                    status=r.status,
                    created_at=r.started_at,
                )
            )
    rows.sort(key=lambda r: r.started_at, reverse=True)
    page = rows[: body.limit]
    return UsageLedgerOut(
        range=RangeOut(preset="custom", start=body.start, end=body.end),
        rows=page,
        next_cursor=None,
    )


def ledger_to_csv(out: UsageLedgerOut) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "event_id",
            "tenant_id",
            "tenant_name",
            "event_type",
            "service_provider",
            "service_name",
            "resource_name",
            "quantity",
            "unit",
            "started_at",
            "ended_at",
            "duration_seconds",
            "actual_cost_inr",
            "estimated_cost_inr",
            "status",
        ]
    )
    for r in out.rows:
        writer.writerow(
            [
                r.event_id,
                r.tenant_id or "",
                r.tenant_name,
                r.event_type,
                r.service_provider,
                r.service_name,
                r.resource_name,
                r.quantity,
                r.unit,
                r.started_at.isoformat(),
                r.ended_at.isoformat() if r.ended_at else "",
                r.duration_seconds or "",
                r.actual_cost_inr if r.actual_cost_inr is not None else "",
                r.estimated_cost_inr if r.estimated_cost_inr is not None else "",
                r.status,
            ]
        )
    return buffer.getvalue()


# ---------------------------------------------------------------- Reports
async def build_report(
    session: AsyncSession,
    report_type: ReportType,
    preset: DatePreset,
    start: date | None,
    end: date | None,
    tenant_id: uuid.UUID | None,
) -> ReportRowOut:
    range_start, range_end = resolve_range(preset, start, end)
    totals = await _collect_range(session, range_start, range_end, tenant_id)
    tenant_rows = (
        await session.execute(_TENANTS_ALL, {"t": str(tenant_id) if tenant_id else None})
    ).all()

    if report_type == "client_usage":
        columns = ["client", "messages_total", "llm_calls", "tasks_total", "total_cost_inr"]
        out_rows = []
        for t in tenant_rows:
            messages = _messages_for_tenant(totals, t.id)
            llm_calls = sum(line.calls for line in totals.llm_lines_by_tenant.get(t.id, []))
            tasks = sum(int(r.n) for r in totals.agent_rows if r.tenant_id == t.id)
            summary, total_cost = _financial_summary(totals, t.id)
            out_rows.append(
                [t.name, str(messages.total), str(llm_calls), str(tasks), str(total_cost)]
            )
        return ReportRowOut(columns=columns, rows=out_rows)

    if report_type == "llm":
        columns = [
            "client",
            "provider",
            "model",
            "calls",
            "input_tokens",
            "output_tokens",
            "cost_inr",
        ]
        out_rows = []
        names = {t.id: t.name for t in tenant_rows}
        for tid, lines in totals.llm_lines_by_tenant.items():
            for line in lines:
                out_rows.append(
                    [
                        names.get(tid, str(tid)),
                        line.provider,
                        line.model,
                        str(line.calls),
                        str(line.input_tokens),
                        str(line.output_tokens),
                        str(_money(line.amount_paise)),
                    ]
                )
        return ReportRowOut(columns=columns, rows=out_rows)

    if report_type == "gcp":
        columns = ["client", "category", "quantity", "unit", "cost_usd"]
        names = {t.id: t.name for t in tenant_rows}
        out_rows = [
            [
                names.get(r.tenant_id, "shared") if r.tenant_id else "shared",
                r.category,
                str(r.qty),
                r.unit,
                str(r.cost_usd),
            ]
            for r in totals.gcp_rows
        ]
        return ReportRowOut(columns=columns, rows=out_rows)

    if report_type == "plivo":
        columns = ["client", "direction", "calls", "billed_seconds", "cost_inr"]
        names = {t.id: t.name for t in tenant_rows}
        out_rows = [
            [
                names.get(r.tenant_id, "unknown") if r.tenant_id else "unknown",
                r.direction,
                str(r.calls),
                str(r.secs),
                str(_money(r.cost)),
            ]
            for r in totals.plivo_rows
        ]
        return ReportRowOut(columns=columns, rows=out_rows)

    if report_type == "agent_performance":
        columns = [
            "client",
            "total_tasks",
            "completed",
            "failed",
            "pending",
            "success_rate_percent",
            "avg_execution_seconds",
            "retry_count",
        ]
        out_rows = []
        for t in tenant_rows:
            llm_cost = _llm_cost_inr_for_tenant(totals, t.id)
            perf = _agent_performance_for_tenant(totals, t.id, llm_cost)
            out_rows.append(
                [
                    t.name,
                    str(perf.total_tasks),
                    str(perf.completed),
                    str(perf.failed),
                    str(perf.pending),
                    str(perf.success_rate_percent),
                    str(perf.avg_execution_seconds),
                    str(perf.retry_count),
                ]
            )
        return ReportRowOut(columns=columns, rows=out_rows)

    if report_type == "profitability":
        columns = ["client", "revenue_inr", "cost_inr", "profit_inr", "margin_percent"]
        out_rows = []
        for t in tenant_rows:
            summary, total_cost = _financial_summary(totals, t.id)
            out_rows.append(
                [
                    t.name,
                    str(summary.client_revenue_inr),
                    str(total_cost),
                    str(summary.gross_profit_inr),
                    str(summary.profit_margin_percent),
                ]
            )
        return ReportRowOut(columns=columns, rows=out_rows)

    if report_type == "actual_vs_estimated":
        columns = [
            "client",
            "cloudrun_basis",
            "cloudrun_paise",
            "telephony_basis",
            "telephony_paise",
        ]
        rows_sql = (
            await session.execute(
                text(
                    "SELECT t.name, i.cloudrun_basis, i.cloudrun_paise, i.telephony_paise"
                    " FROM invoices i JOIN tenants t ON t.id = i.tenant_id"
                    " WHERE i.invoice_date >= :s AND i.invoice_date <= :e"
                    " AND (CAST(:tid AS uuid) IS NULL OR i.tenant_id = CAST(:tid AS uuid))"
                    " ORDER BY t.name"
                ),
                {"s": range_start, "e": range_end, "tid": str(tenant_id) if tenant_id else None},
            )
        ).all()
        out_rows = [
            [r.name, r.cloudrun_basis, str(r.cloudrun_paise), "n/a", str(r.telephony_paise)]
            for r in rows_sql
        ]
        return ReportRowOut(columns=columns, rows=out_rows)

    # unallocated
    columns = ["billing_period", "category", "amount_usd", "reason", "resolved"]
    rows_sql = (
        await session.execute(
            text(
                "SELECT billing_period, category, amount_usd, reason, resolved"
                " FROM gcp_unallocated_costs WHERE created_at >= :s AND created_at < :e"
                " ORDER BY billing_period DESC, amount_usd DESC"
            ),
            {"s": _bounds(range_start, range_end)[0], "e": _bounds(range_start, range_end)[1]},
        )
    ).all()
    out_rows = [
        [str(r.billing_period), r.category, str(r.amount_usd), r.reason, str(r.resolved)]
        for r in rows_sql
    ]
    return ReportRowOut(columns=columns, rows=out_rows)


def report_to_csv(report: ReportRowOut) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(report.columns)
    writer.writerows(report.rows)
    return buffer.getvalue()


# ---------------------------------------------------------------- Usage limits + alerts
_LIMIT_UPSERT = text(
    "INSERT INTO usage_limits (tenant_id, metric, limit_value, active, updated_by, updated_at)"
    " VALUES (:t, :metric, :limit, :active, :u, now())"
    " ON CONFLICT (tenant_id, metric) DO UPDATE SET limit_value = :limit, active = :active,"
    " updated_by = :u, updated_at = now()"
    " RETURNING id, tenant_id, metric, limit_value, active, updated_at"
)
_LIMITS_LIST = text(
    "SELECT id, tenant_id, metric, limit_value, active, updated_at FROM usage_limits"
    " WHERE (CAST(:t AS uuid) IS NULL OR tenant_id = CAST(:t AS uuid)) ORDER BY tenant_id, metric"
)


async def save_usage_limit(
    session: AsyncSession, body: UsageLimitIn, admin_id: uuid.UUID
) -> UsageLimitOut:
    row = (
        await session.execute(
            _LIMIT_UPSERT,
            {
                "t": body.tenant_id,
                "metric": body.metric,
                "limit": body.limit_value,
                "active": body.active,
                "u": admin_id,
            },
        )
    ).one()
    return UsageLimitOut(
        id=row.id,
        tenant_id=row.tenant_id,
        metric=row.metric,
        limit_value=row.limit_value,
        active=row.active,
        updated_at=row.updated_at,
    )


async def list_usage_limits(session: AsyncSession, tenant_id: uuid.UUID | None) -> UsageLimitsOut:
    rows = (await session.execute(_LIMITS_LIST, {"t": str(tenant_id) if tenant_id else None})).all()
    return UsageLimitsOut(
        limits=[
            UsageLimitOut(
                id=r.id,
                tenant_id=r.tenant_id,
                metric=r.metric,
                limit_value=r.limit_value,
                active=r.active,
                updated_at=r.updated_at,
            )
            for r in rows
        ]
    )


async def usage_limit_statuses(session: AsyncSession) -> UsageLimitStatusesOut:
    """Current-calendar-month usage against each configured, active limit. Used by both the
    Billing > Alerts panel and core.alerts.scan_and_upsert() (same numbers, no duplicate
    calculation)."""
    today = timeutil.to_ist(timeutil.utc_now()).date()
    month_start = today.replace(day=1)
    totals = await _collect_range(session, month_start, today, None)
    limits = (await session.execute(_LIMITS_LIST, {"t": None})).all()
    names = {r.id: r.name for r in (await session.execute(_TENANTS_ALL, {"t": None})).all()}
    statuses: list[UsageLimitStatusOut] = []
    for lim in limits:
        if not lim.active:
            continue
        if lim.metric == "messages_month":
            current = Decimal(_messages_for_tenant(totals, lim.tenant_id).total)
        elif lim.metric == "llm_cost_inr_month":
            current = _llm_cost_inr_for_tenant(totals, lim.tenant_id)
        else:
            summary, total_cost = _financial_summary(totals, lim.tenant_id)
            current = total_cost
        percent = _pct(current, Decimal(lim.limit_value))
        statuses.append(
            UsageLimitStatusOut(
                tenant_id=lim.tenant_id,
                tenant_name=names.get(lim.tenant_id, str(lim.tenant_id)),
                metric=lim.metric,
                limit_value=Decimal(lim.limit_value),
                current_value=current,
                percent_used=percent,
                breached_70=percent >= 70,
                breached_90=percent >= 90,
                breached_100=percent >= 100,
            )
        )
    return UsageLimitStatusesOut(statuses=statuses)


# ---------------------------------------------------------------- GCP Billing Export status
_IMPORT_BATCHES = text("SELECT count(DISTINCT import_batch_id) FROM gcp_billing_import_rows")


async def billing_export_status(session: AsyncSession) -> BillingExportSyncStatusOut:
    settings = await billing_service.load_settings(session)
    row = (
        await session.execute(
            text(
                "SELECT billing_export_last_sync_at, billing_export_last_sync_rows,"
                " billing_export_sync_error FROM billing_settings WHERE id = 1"
            )
        )
    ).first()
    batches = (await session.execute(_IMPORT_BATCHES)).scalar_one()
    return BillingExportSyncStatusOut(
        status=settings.billing_export_status,
        project_ref=settings.billing_export_ref,
        last_sync_at=row.billing_export_last_sync_at if row else None,
        last_sync_rows=int(row.billing_export_last_sync_rows) if row else 0,
        last_error=row.billing_export_sync_error if row else "",
        import_batches=int(batches),
    )


# ---------------------------------------------------------------- Client-facing own-usage view
async def client_usage(session: AsyncSession, tenant_id: uuid.UUID) -> ClientUsageOut:
    today = timeutil.to_ist(timeutil.utc_now()).date()
    month_start = today.replace(day=1)
    detail = await client_detail(session, tenant_id, "custom", month_start, today)
    history_rows = (
        await session.execute(
            text(
                "SELECT i.llm_paise, i.cloudrun_paise, i.telephony_paise, i.speech_paise,"
                " i.total_paise FROM invoices i WHERE i.tenant_id = :t AND i.status = 'final'"
                " ORDER BY i.period_start DESC LIMIT 12"
            ),
            {"t": tenant_id},
        )
    ).all()
    history = [
        ClientFinancialSummaryOut(
            total_llm_cost_inr=_money(r.llm_paise),
            total_cloud_cost_inr=_money(r.cloudrun_paise),
            total_calling_cost_inr=_money(r.telephony_paise),
            total_other_cost_inr=_money(r.speech_paise),
            total_operating_cost_inr=_money(
                r.llm_paise + r.cloudrun_paise + r.telephony_paise + r.speech_paise
            ),
            client_revenue_inr=_money(r.total_paise),
            gross_profit_inr=_ZERO,
            profit_margin_percent=_ZERO,
        )
        for r in history_rows
    ]
    return ClientUsageOut(
        range=detail.range,
        messages=detail.messages,
        agent_performance=detail.agent_performance,
        llm_cost_inr=detail.financial_summary.total_llm_cost_inr,
        cloud_cost_inr=detail.financial_summary.total_cloud_cost_inr,
        calling_cost_inr=detail.financial_summary.total_calling_cost_inr,
        total_usage_cost_inr=detail.financial_summary.total_operating_cost_inr,
        billing_history=history,
    )
