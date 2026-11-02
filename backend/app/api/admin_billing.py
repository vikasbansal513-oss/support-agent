import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import require_admin
from app.auth.service import Principal
from app.billing import calc, service
from app.billing import usage as usage_service
from app.billing.pdf import render_invoice_pdf
from app.billing.schemas import (
    BillingSettingsIn,
    BillingSettingsOut,
    CancelInvoiceIn,
    CostCategoriesOut,
    CostCategoryIn,
    CostCategoryOut,
    FeeIn,
    FinalizeIn,
    GcpImportIn,
    GcpImportOut,
    GcpReconciliationOut,
    GcpUnallocatedListOut,
    GcpUsageEventIn,
    GcpUsageEventOut,
    GcpUsageEventsOut,
    GenerateIn,
    GenerateOut,
    InvoiceOut,
    LlmRateIn,
    LlmRateOut,
    LlmRatesOut,
    PaidIn,
    PeriodIn,
    PeriodOut,
    PlivoCallsOut,
    PlivoNumberAssignmentIn,
    PlivoNumberAssignmentOut,
    ProfileIn,
    ProfileRowOut,
    RecordPaymentIn,
    ResolveUnallocatedIn,
    SpeechRateIn,
    SpeechRateOut,
    SpeechRatesOut,
    SpeechUsageIn,
    SpeechUsageListOut,
    SpeechUsageOut,
)
from app.billing.usage_schemas import (
    BillingExportSyncStatusOut,
    ClientBillingOut,
    ClientBillingSummaryOut,
    DatePreset,
    OverviewOut,
    ReportType,
    UsageLedgerFilterIn,
    UsageLedgerOut,
    UsageLimitIn,
    UsageLimitOut,
    UsageLimitsOut,
    UsageLimitStatusesOut,
)
from app.core.db import get_session_factory, session_scope
from app.core.errors import AppError

router = APIRouter(prefix="/admin/billing", tags=["admin-billing"])
Factory = async_sessionmaker[AsyncSession]


def _period(value: str) -> date:
    try:
        return calc.parse_period(value)
    except ValueError as exc:
        raise AppError("E-VAL-001", detail="bad period") from exc


@router.get("/settings")
async def read_settings(
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> BillingSettingsOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.load_settings(session)


@router.put("/settings")
async def save_settings(
    body: BillingSettingsIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> BillingSettingsOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.save_settings(session, body, admin.user_id)


@router.get("/llm-rates")
async def read_rates(
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> LlmRatesOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.load_rates(session)


@router.put("/llm-rates")
async def save_rate(
    body: LlmRateIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> LlmRateOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.save_rate(session, body, admin.user_id)


@router.get("/speech-rates")
async def read_speech_rates(
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> SpeechRatesOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.load_speech_rates(session)


@router.put("/speech-rates")
async def save_speech_rate(
    body: SpeechRateIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> SpeechRateOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.save_speech_rate(session, body, admin.user_id)


@router.get("/cost-categories")
async def read_cost_categories(
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> CostCategoriesOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.load_cost_categories(session)


@router.put("/cost-categories")
async def save_cost_category(
    body: CostCategoryIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> CostCategoryOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.save_cost_category(session, body, admin.user_id)


@router.get("/profiles")
async def list_profiles(
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> list[ProfileRowOut]:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.list_profiles(session)


@router.put("/profiles/{tenant_id}")
async def save_profile(
    tenant_id: uuid.UUID,
    body: ProfileIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> ProfileRowOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.save_profile(session, tenant_id, body, admin.user_id)


@router.get("/periods/{period}")
async def read_period(
    period: str,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> PeriodOut:
    period_start = _period(period)
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.period_overview(session, period_start)


@router.put("/periods/{period}")
async def save_period(
    period: str,
    body: PeriodIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> PeriodOut:
    period_start = _period(period)
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.save_period(session, period_start, body, admin.user_id)


@router.post("/invoices/generate")
async def generate_invoices(
    body: GenerateIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> GenerateOut:
    period_start = _period(body.period)
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.generate(session, period_start, body.tenant_id, admin.user_id)


@router.get("/invoices/{invoice_id}")
async def read_invoice(
    invoice_id: uuid.UUID,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> InvoiceOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.get_invoice(session, invoice_id)


@router.put("/invoices/{invoice_id}/fee")
async def set_fee(
    invoice_id: uuid.UUID,
    body: FeeIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> InvoiceOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.set_fee(session, invoice_id, body.platform_fee_paise, admin.user_id)


@router.post("/invoices/{invoice_id}/finalize")
async def finalize_invoice(
    invoice_id: uuid.UUID,
    body: FinalizeIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> InvoiceOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.finalize(session, invoice_id, body.expected_total_paise, admin.user_id)


@router.put("/invoices/{invoice_id}/paid")
async def set_paid(
    invoice_id: uuid.UUID,
    body: PaidIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> InvoiceOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.mark_paid(session, invoice_id, body.paid, admin.user_id)


@router.get("/invoices/{invoice_id}/pdf")
async def download_invoice_pdf(
    invoice_id: uuid.UUID,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> Response:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        invoice = await service.get_invoice(session, invoice_id)
    pdf_bytes = render_invoice_pdf(invoice)
    filename = invoice.invoice_no or f"draft-{invoice.id}"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'},
    )


@router.post("/invoices/{invoice_id}/payments")
async def record_payment(
    invoice_id: uuid.UUID,
    body: RecordPaymentIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> InvoiceOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.record_payment(session, invoice_id, body, admin.user_id)


@router.delete("/invoices/{invoice_id}/payments/{payment_id}")
async def delete_payment(
    invoice_id: uuid.UUID,
    payment_id: uuid.UUID,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> InvoiceOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.delete_payment(session, invoice_id, payment_id, admin.user_id)


@router.post("/invoices/{invoice_id}/cancel")
async def cancel_invoice(
    invoice_id: uuid.UUID,
    body: CancelInvoiceIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> InvoiceOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.cancel_invoice(session, invoice_id, body, admin.user_id)


@router.post("/invoices/{invoice_id}/uncancel")
async def uncancel_invoice(
    invoice_id: uuid.UUID,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> InvoiceOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.uncancel_invoice(session, invoice_id, admin.user_id)


# ---------------------------------------------------------------- Plivo numbers + CDR
@router.get("/plivo/numbers")
async def list_plivo_numbers(
    tenant_id: uuid.UUID | None = None,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> list[PlivoNumberAssignmentOut]:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.list_plivo_assignments(session, tenant_id)


@router.post("/plivo/numbers")
async def assign_plivo_number(
    body: PlivoNumberAssignmentIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> PlivoNumberAssignmentOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.save_plivo_assignment(session, body, admin.user_id)


@router.get("/plivo/calls/{period}")
async def list_plivo_calls(
    period: str,
    tenant_id: uuid.UUID | None = None,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> PlivoCallsOut:
    period_start = _period(period)
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.list_plivo_calls(session, period_start, tenant_id)


# ---------------------------------------------------------------- STT/TTS per-call usage
@router.post("/speech-usage")
async def record_speech_usage(
    body: SpeechUsageIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> SpeechUsageOut | None:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.record_speech_usage(session, body, admin.user_id)


@router.get("/speech-usage/{period}")
async def list_speech_usage(
    period: str,
    tenant_id: uuid.UUID | None = None,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> SpeechUsageListOut:
    period_start = _period(period)
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.list_speech_usage(session, period_start, tenant_id)


# ---------------------------------------------------------------- GCP app-level metering
@router.post("/gcp/usage-events")
async def record_gcp_usage_event(
    body: GcpUsageEventIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> GcpUsageEventOut | None:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.record_gcp_usage_event(session, body, admin.user_id)


@router.get("/gcp/usage-events/{period}")
async def list_gcp_usage_events(
    period: str,
    tenant_id: uuid.UUID | None = None,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> GcpUsageEventsOut:
    period_start = _period(period)
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.list_gcp_usage_events(session, period_start, tenant_id)


# ---------------------------------------------------------------- GCP Billing Export import
@router.post("/gcp/import")
async def import_gcp_billing(
    body: GcpImportIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> GcpImportOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.import_gcp_billing(session, body, admin.user_id)


@router.post("/gcp/reconcile/{period}")
async def run_reconciliation(
    period: str,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> GcpReconciliationOut:
    period_start = _period(period)
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.run_reconciliation(session, period_start, admin.user_id)


@router.get("/gcp/reconcile/{period}")
async def read_reconciliation(
    period: str,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> GcpReconciliationOut | None:
    period_start = _period(period)
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.load_reconciliation(session, period_start)


@router.get("/gcp/unallocated/{period}")
async def list_unallocated(
    period: str,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> GcpUnallocatedListOut:
    period_start = _period(period)
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await service.list_unallocated(session, period_start)


@router.put("/gcp/unallocated/{item_id}")
async def resolve_unallocated(
    item_id: uuid.UUID,
    body: ResolveUnallocatedIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> dict[str, bool]:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        await service.resolve_unallocated(session, item_id, body.resolved, admin.user_id)
    return {"ok": True}


# ================================================================== Usage Intelligence
# (sections A-C, F, H, J of the usage-metering spec -- all still under Admin -> Billing,
# per the "do not create another top-level menu" requirement)

# ---------------------------------------------------------------- A. Billing Overview
@router.get("/overview")
async def read_overview(
    preset: DatePreset = Query(default="this_month"),  # noqa: B008
    start: date | None = None,
    end: date | None = None,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> OverviewOut:
    """Date-range-filterable financial overview (Total Revenue/Cost/Profit/Margin) --
    distinct from the existing monthly-only GET /periods/{period} (invoice generation uses
    that one; this one is read-only platform-wide intelligence for any date range)."""
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await usage_service.overview(session, preset, start, end)


# ---------------------------------------------------------------- B. Client-wise billing
@router.get("/clients")
async def list_clients(
    preset: DatePreset = Query(default="this_month"),  # noqa: B008
    start: date | None = None,
    end: date | None = None,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> list[ClientBillingSummaryOut]:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await usage_service.list_clients(session, preset, start, end)


@router.get("/clients/{tenant_id}")
async def read_client(
    tenant_id: uuid.UUID,
    preset: DatePreset = Query(default="this_month"),  # noqa: B008
    start: date | None = None,
    end: date | None = None,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> ClientBillingOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await usage_service.client_detail(session, tenant_id, preset, start, end)


# ---------------------------------------------------------------- C. Usage Ledger
def _ledger_filter(
    tenant_id: uuid.UUID | None,
    event_type: str | None,
    service_provider: str | None,
    service_name: str | None,
    start: date,
    end: date,
    limit: int,
) -> UsageLedgerFilterIn:
    return UsageLedgerFilterIn(
        tenant_id=tenant_id,
        event_type=event_type,  # type: ignore[arg-type]
        service_provider=service_provider,
        service_name=service_name,
        start=start,
        end=end,
        limit=limit,
    )


@router.get("/usage-ledger")
async def read_usage_ledger(
    start: date,
    end: date,
    tenant_id: uuid.UUID | None = None,
    event_type: str | None = None,
    service_provider: str | None = None,
    service_name: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> UsageLedgerOut:
    body = _ledger_filter(tenant_id, event_type, service_provider, service_name, start, end, limit)
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await usage_service.usage_ledger(session, body)


@router.get("/usage-ledger/export.csv")
async def export_usage_ledger_csv(
    start: date,
    end: date,
    tenant_id: uuid.UUID | None = None,
    event_type: str | None = None,
    service_provider: str | None = None,
    service_name: str | None = None,
    limit: int = Query(default=500, ge=1, le=500),
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> Response:
    body = _ledger_filter(tenant_id, event_type, service_provider, service_name, start, end, limit)
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        out = await usage_service.usage_ledger(session, body)
    csv_text = usage_service.ledger_to_csv(out)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="usage-ledger.csv"'},
    )


# ---------------------------------------------------------------- Reports (section 9)
# Excel: a plain CSV opens natively in Excel/Sheets; this project has no openpyxl/xlsxwriter
# dependency, so "Excel export" here means a CSV Excel can open, same convention already used
# for /admin/audit-log/export.csv. Not a missing feature -- see README "Abhi verify nahi hua"
# if a real .xlsx is ever required.
@router.get("/reports/{report_type}")
async def read_report(
    report_type: ReportType,
    preset: DatePreset = Query(default="this_month"),  # noqa: B008
    start: date | None = None,
    end: date | None = None,
    tenant_id: uuid.UUID | None = None,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> dict[str, list[str] | list[list[str]]]:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        report = await usage_service.build_report(session, report_type, preset, start, end, tenant_id)
    return {"columns": report.columns, "rows": report.rows}


@router.get("/reports/{report_type}/export.csv")
async def export_report_csv(
    report_type: ReportType,
    preset: DatePreset = Query(default="this_month"),  # noqa: B008
    start: date | None = None,
    end: date | None = None,
    tenant_id: uuid.UUID | None = None,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> Response:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        report = await usage_service.build_report(session, report_type, preset, start, end, tenant_id)
    csv_text = usage_service.report_to_csv(report)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{report_type}.csv"'},
    )


# ---------------------------------------------------------------- Usage limits + alerts (10)
@router.get("/usage-limits")
async def list_usage_limits(
    tenant_id: uuid.UUID | None = None,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> UsageLimitsOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await usage_service.list_usage_limits(session, tenant_id)


@router.put("/usage-limits")
async def save_usage_limit(
    body: UsageLimitIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> UsageLimitOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await usage_service.save_usage_limit(session, body, admin.user_id)


@router.get("/usage-limits/status")
async def usage_limit_status(
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> UsageLimitStatusesOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await usage_service.usage_limit_statuses(session)


# ---------------------------------------------------------------- GCP Billing Export status (6)
@router.get("/gcp/export-status")
async def gcp_export_status(
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> BillingExportSyncStatusOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return await usage_service.billing_export_status(session)
