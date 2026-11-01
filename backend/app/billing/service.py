import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import calc
from app.billing.schemas import (
    BillingSettingsIn,
    BillingSettingsOut,
    BuyerOut,
    CancelInvoiceIn,
    CostCategoriesOut,
    CostCategoryIn,
    CostCategoryOut,
    FlagOut,
    GcpImportIn,
    GcpImportOut,
    GcpReconciliationOut,
    GcpUnallocatedListOut,
    GcpUnallocatedOut,
    GcpUsageEventIn,
    GcpUsageEventOut,
    GcpUsageEventsOut,
    GenerateOut,
    InvoiceLineOut,
    InvoiceOut,
    InvoicePaymentOut,
    InvoiceSummaryOut,
    LlmRateIn,
    LlmRateOut,
    LlmRatesOut,
    ModelRef,
    PaymentStatus,
    PeriodIn,
    PeriodOut,
    PeriodRowOut,
    PlivoCallOut,
    PlivoCallsOut,
    PlivoNumberAssignmentIn,
    PlivoNumberAssignmentOut,
    ProfileIn,
    ProfileRowOut,
    RecordPaymentIn,
    SellerOut,
    SpeechRateIn,
    SpeechRateOut,
    SpeechRatesOut,
    SpeechUsageIn,
    SpeechUsageListOut,
    SpeechUsageOut,
    TelephonyBasis,
)
from app.core import timeutil
from app.core.audit import AuditEvent, record
from app.core.errors import AppError

_ZERO = Decimal(0)
_MILLION = Decimal(1_000_000)
_SECONDS_PER_HOUR = Decimal(3600)

_SETTINGS = text(
    "SELECT seller_name, seller_address, seller_state_code, seller_gstin, seller_email,"
    " seller_phone, seller_pincode, payment_details, sac_code, invoice_prefix, gst_enabled,"
    " gst_rate_percent, cloudrun_vcpu, cloudrun_gib, cloudrun_usd_per_vcpu_second,"
    " cloudrun_usd_per_gib_second, billing_export_status, billing_export_ref,"
    " reconciliation_cycle, unallocated_handling, shared_allocation_policy,"
    " plivo_outbound_inr_per_min, plivo_inbound_inr_per_min,"
    " plivo_number_rental_inr_per_month, updated_at FROM billing_settings WHERE id = 1"
)
_SETTINGS_UPSERT = text(
    "INSERT INTO billing_settings (id, seller_name, seller_address, seller_state_code,"
    " seller_gstin, seller_email, seller_phone, seller_pincode, payment_details, sac_code,"
    " invoice_prefix, gst_enabled, gst_rate_percent, cloudrun_vcpu, cloudrun_gib,"
    " cloudrun_usd_per_vcpu_second, cloudrun_usd_per_gib_second, billing_export_status,"
    " billing_export_ref, reconciliation_cycle, unallocated_handling, shared_allocation_policy,"
    " plivo_outbound_inr_per_min, plivo_inbound_inr_per_min,"
    " plivo_number_rental_inr_per_month, updated_by, updated_at) VALUES (1, :seller_name,"
    " :seller_address, :seller_state_code, :seller_gstin, :seller_email, :seller_phone,"
    " :seller_pincode, :payment_details, :sac_code, :invoice_prefix, :gst_enabled,"
    " :gst_rate_percent, :cloudrun_vcpu, :cloudrun_gib, :cloudrun_usd_per_vcpu_second,"
    " :cloudrun_usd_per_gib_second, :billing_export_status, :billing_export_ref,"
    " :reconciliation_cycle, :unallocated_handling, :shared_allocation_policy,"
    " :plivo_outbound_inr_per_min, :plivo_inbound_inr_per_min,"
    " :plivo_number_rental_inr_per_month, :updated_by, now())"
    " ON CONFLICT (id) DO UPDATE SET seller_name = :seller_name, seller_address = :seller_address,"
    " seller_state_code = :seller_state_code, seller_gstin = :seller_gstin,"
    " seller_email = :seller_email, seller_phone = :seller_phone,"
    " seller_pincode = :seller_pincode,"
    " payment_details = :payment_details, sac_code = :sac_code, invoice_prefix = :invoice_prefix,"
    " gst_enabled = :gst_enabled, gst_rate_percent = :gst_rate_percent,"
    " cloudrun_vcpu = :cloudrun_vcpu, cloudrun_gib = :cloudrun_gib,"
    " cloudrun_usd_per_vcpu_second = :cloudrun_usd_per_vcpu_second,"
    " cloudrun_usd_per_gib_second = :cloudrun_usd_per_gib_second,"
    " billing_export_status = :billing_export_status, billing_export_ref = :billing_export_ref,"
    " reconciliation_cycle = :reconciliation_cycle,"
    " unallocated_handling = :unallocated_handling,"
    " shared_allocation_policy = :shared_allocation_policy,"
    " plivo_outbound_inr_per_min = :plivo_outbound_inr_per_min,"
    " plivo_inbound_inr_per_min = :plivo_inbound_inr_per_min,"
    " plivo_number_rental_inr_per_month = :plivo_number_rental_inr_per_month,"
    " updated_by = :updated_by, updated_at = now() RETURNING updated_at"
)
_COST_CATEGORIES = text(
    "SELECT category, unit, usd_rate, attribution, active, sort_order, updated_at"
    " FROM billing_cost_categories ORDER BY sort_order, category"
)
_COST_CATEGORY_UPSERT = text(
    "INSERT INTO billing_cost_categories (category, unit, usd_rate, attribution, active,"
    " sort_order, updated_by, updated_at) VALUES (:category, :unit, :usd_rate, :attribution,"
    " :active, COALESCE((SELECT sort_order FROM billing_cost_categories"
    " WHERE category = :category), (SELECT COALESCE(max(sort_order), 0) + 1"
    " FROM billing_cost_categories)), :u, now())"
    " ON CONFLICT (category) DO UPDATE SET unit = :unit, usd_rate = :usd_rate,"
    " attribution = :attribution, active = :active, updated_by = :u, updated_at = now()"
    " RETURNING category, unit, usd_rate, attribution, active, sort_order, updated_at"
)
_RATES = text(
    "SELECT provider, model, input_usd_per_million, output_usd_per_million, updated_at"
    " FROM billing_llm_rates ORDER BY provider, model"
)
_RATE_UPSERT = text(
    "INSERT INTO billing_llm_rates (provider, model, input_usd_per_million,"
    " output_usd_per_million, updated_by, updated_at) VALUES (:provider, :model, :i, :o, :u, now())"
    " ON CONFLICT (provider, model) DO UPDATE SET input_usd_per_million = :i,"
    " output_usd_per_million = :o, updated_by = :u, updated_at = now()"
    " RETURNING input_usd_per_million, output_usd_per_million, updated_at"
)
_UNPRICED = text(
    "SELECT DISTINCT c.provider, c.model FROM llm_calls c WHERE c.created_at >= :since"
    " AND (c.input_tokens > 0 OR c.output_tokens > 0) AND NOT EXISTS"
    " (SELECT 1 FROM billing_llm_rates r WHERE r.provider = c.provider AND r.model = c.model)"
    " ORDER BY c.provider, c.model"
)
_SPEECH_RATES = text(
    "SELECT provider, unit, rate_inr, active, updated_at FROM billing_speech_rates"
    " ORDER BY provider"
)
_SPEECH_RATE_UPSERT = text(
    "INSERT INTO billing_speech_rates (provider, unit, rate_inr, active, updated_by, updated_at)"
    " VALUES (:provider, :unit, :rate, :active, :u, now())"
    " ON CONFLICT (provider) DO UPDATE SET unit = :unit, rate_inr = :rate, active = :active,"
    " updated_by = :u, updated_at = now()"
    " RETURNING provider, unit, rate_inr, active, updated_at"
)
_PROFILES = text(
    "SELECT t.id AS tenant_id, t.name AS tenant_name, t.slug, COALESCE(p.legal_name, '') AS legal_name,"
    " COALESCE(p.address, '') AS address, COALESCE(p.state_code, '') AS state_code,"
    " COALESCE(p.gstin, '') AS gstin, COALESCE(p.email, '') AS email,"
    " COALESCE(p.default_platform_fee_paise, 0) AS default_platform_fee_paise,"
    " COALESCE(p.cloud_enabled, true) AS cloud_enabled,"
    " COALESCE(p.llm_enabled, true) AS llm_enabled,"
    " COALESCE(p.plivo_enabled, true) AS plivo_enabled,"
    " COALESCE(p.payment_due_days, 15) AS payment_due_days,"
    " COALESCE(p.auto_generate_draft, true) AS auto_generate_draft,"
    " COALESCE(p.billing_status, 'enabled') AS billing_status"
    " FROM tenants t LEFT JOIN tenant_billing_profiles p ON p.tenant_id = t.id"
    " WHERE (CAST(:t AS uuid) IS NULL OR t.id = CAST(:t AS uuid)) ORDER BY t.name, t.id"
)
_PROFILE_UPSERT = text(
    "INSERT INTO tenant_billing_profiles (tenant_id, legal_name, address, state_code, gstin,"
    " email, default_platform_fee_paise, cloud_enabled, llm_enabled, plivo_enabled,"
    " payment_due_days, auto_generate_draft, billing_status, updated_by, updated_at)"
    " VALUES (:t, :legal_name, :address, :state_code, :gstin, :email,"
    " :default_platform_fee_paise, :cloud_enabled, :llm_enabled, :plivo_enabled,"
    " :payment_due_days, :auto_generate_draft, :billing_status, :u, now())"
    " ON CONFLICT (tenant_id) DO UPDATE SET legal_name = :legal_name, address = :address,"
    " state_code = :state_code, gstin = :gstin, email = :email,"
    " default_platform_fee_paise = :default_platform_fee_paise,"
    " cloud_enabled = :cloud_enabled, llm_enabled = :llm_enabled,"
    " plivo_enabled = :plivo_enabled, payment_due_days = :payment_due_days,"
    " auto_generate_draft = :auto_generate_draft, billing_status = :billing_status,"
    " updated_by = :u, updated_at = now()"
)
_PERIOD = text(
    "SELECT usd_inr, cloudrun_actual_paise, telephony_actual_paise FROM billing_periods"
    " WHERE period_start = :p"
)
_PERIOD_UPSERT = text(
    "INSERT INTO billing_periods (period_start, usd_inr, cloudrun_actual_paise,"
    " telephony_actual_paise, updated_by, updated_at) VALUES (:p, :fx, :actual, :tel_actual, :u,"
    " now()) ON CONFLICT (period_start) DO UPDATE SET usd_inr = :fx, cloudrun_actual_paise ="
    " :actual, telephony_actual_paise = :tel_actual, updated_by = :u, updated_at = now()"
)
_LLM_USAGE = text(
    "SELECT tenant_id, provider, model, count(*) AS calls,"
    " COALESCE(sum(input_tokens), 0) AS input_tokens,"
    " COALESCE(sum(output_tokens), 0) AS output_tokens,"
    " COALESCE(sum(est_cost_micro), 0) AS est_cost_micro,"
    " count(*) FILTER (WHERE est_cost_micro IS NULL AND (input_tokens > 0 OR output_tokens > 0))"
    " AS unpriced_calls FROM llm_calls WHERE created_at >= :s AND created_at < :e"
    " AND (CAST(:t AS uuid) IS NULL OR tenant_id = CAST(:t AS uuid))"
    " GROUP BY tenant_id, provider, model"
)
_COMPUTE_USAGE = text(
    "SELECT tenant_id, COALESCE(sum(duration_ms), 0) AS ms, count(*) AS jobs FROM jobs"
    " WHERE status = 'done' AND finished_at >= :s AND finished_at < :e GROUP BY tenant_id"
)
_VOICE_USAGE = text(
    "SELECT tenant_id, count(*) AS calls,"
    " COALESCE(sum(extract(epoch FROM (ended_at - started_at))), 0)::bigint AS seconds"
    " FROM voice_calls WHERE ended_at IS NOT NULL AND started_at >= :s AND started_at < :e"
    " GROUP BY tenant_id"
)
_TENANT = text("SELECT id, name FROM tenants WHERE id = :t")
_INVOICE_LOCK = text(
    "SELECT id, status, platform_fee_paise FROM invoices"
    " WHERE tenant_id = :t AND period_start = :p FOR UPDATE"
)
_INVOICE_UPSERT = text(
    "INSERT INTO invoices (tenant_id, period_start, usd_inr, cloudrun_basis, llm_paise,"
    " cloudrun_paise, telephony_paise, speech_paise, platform_fee_paise, subtotal_paise,"
    " gst_rate_percent, gst_kind, cgst_paise, sgst_paise, igst_paise, total_paise, generated_at)"
    " VALUES (:t, :p, :fx, :basis, :llm, :cloud, :tel, :speech, :fee, :sub, :rate, :kind, :cgst,"
    " :sgst, :igst, :total, now())"
    " ON CONFLICT (tenant_id, period_start) DO UPDATE SET usd_inr = :fx, cloudrun_basis = :basis,"
    " llm_paise = :llm, cloudrun_paise = :cloud, telephony_paise = :tel, speech_paise = :speech,"
    " platform_fee_paise = :fee,"
    " subtotal_paise = :sub, gst_rate_percent = :rate, gst_kind = :kind, cgst_paise = :cgst,"
    " sgst_paise = :sgst, igst_paise = :igst, total_paise = :total, generated_at = now()"
    " RETURNING id"
)
_LINES_DELETE = text("DELETE FROM invoice_lines WHERE invoice_id = :i")
_LINE_INSERT = text(
    "INSERT INTO invoice_lines (tenant_id, invoice_id, position, kind, description, quantity,"
    " unit, amount_paise, detail) VALUES (:t, :i, :pos, :kind, :descr, :qty, :unit, :amount,"
    " CAST(:detail AS jsonb))"
)
_PAID_AMOUNT = (
    "COALESCE((SELECT sum(amount_paise) FROM invoice_payments WHERE invoice_id = i.id), 0)"
)
_INVOICE = text(
    "SELECT i.id, i.tenant_id, t.name AS tenant_name, i.period_start, i.status, i.doc_type,"  # noqa: S608
    " i.invoice_no, i.invoice_date, i.due_date, i.paid_at, i.cancelled_at, i.cancelled_reason,"
    f" {_PAID_AMOUNT} AS paid_amount_paise, i.usd_inr, i.cloudrun_basis,"
    " i.llm_paise, i.cloudrun_paise, i.telephony_paise, i.speech_paise, i.platform_fee_paise,"
    " i.subtotal_paise,"
    " i.gst_rate_percent, i.gst_kind, i.cgst_paise, i.sgst_paise, i.igst_paise, i.total_paise,"
    " i.seller, i.buyer, i.generated_at,"
    " i.finalized_at FROM invoices i JOIN tenants t ON t.id = i.tenant_id WHERE i.id = :i"
)
_INVOICE_LOCKED = text(
    "SELECT tenant_id, period_start, status, platform_fee_paise FROM invoices"
    " WHERE id = :i FOR UPDATE"
)
_INVOICES_FOR_PERIOD = text(
    "SELECT i.tenant_id, i.id, i.status, i.invoice_no, i.due_date, i.paid_at, i.total_paise,"  # noqa: S608
    " i.cancelled_at, "
    f"{_PAID_AMOUNT} AS paid_amount_paise"
    " FROM invoices i WHERE i.period_start = :p"
)
_MARK_PAID = text(
    "UPDATE invoices SET paid_at = CASE WHEN :paid THEN now() ELSE NULL END,"
    " paid_by = CASE WHEN :paid THEN :u ELSE NULL END"
    " WHERE id = :i AND status = 'final' RETURNING tenant_id"
)
_PAYMENTS_DELETE = text("DELETE FROM invoice_payments WHERE invoice_id = :i")
_PAYMENT_INSERT = text(
    "INSERT INTO invoice_payments (tenant_id, invoice_id, amount_paise, paid_on, method, note,"
    " created_by) VALUES (:t, :i, :amount, :paid_on, :method, :note, :u) RETURNING id, amount_paise,"
    " paid_on, method, note, created_at"
)
_PAYMENT_DELETE_ONE = text(
    "DELETE FROM invoice_payments WHERE id = :pid AND invoice_id = :i RETURNING tenant_id"
)
_PAYMENTS_FOR_INVOICE = text(
    "SELECT id, amount_paise, paid_on, method, note, created_at FROM invoice_payments"
    " WHERE invoice_id = :i ORDER BY paid_on, created_at"
)
_CANCEL_INVOICE = text(
    "UPDATE invoices SET cancelled_at = now(), cancelled_by = :u, cancelled_reason = :reason"
    " WHERE id = :i AND status = 'final' AND cancelled_at IS NULL RETURNING tenant_id"
)
_UNCANCEL_INVOICE = text(
    "UPDATE invoices SET cancelled_at = NULL, cancelled_by = NULL, cancelled_reason = ''"
    " WHERE id = :i AND status = 'final' RETURNING tenant_id"
)
_LINES = text(
    "SELECT position, kind, description, quantity, unit, amount_paise, detail"
    " FROM invoice_lines WHERE invoice_id = :i ORDER BY position"
)
_COUNTER = text(
    "INSERT INTO invoice_counters (fy, last_no) VALUES (:fy, 1)"
    " ON CONFLICT (fy) DO UPDATE SET last_no = invoice_counters.last_no + 1 RETURNING last_no"
)
_FINALIZE = text(
    "UPDATE invoices SET status = 'final', doc_type = :doc, invoice_no = :no,"
    " invoice_date = :on, due_date = :due, seller = CAST(:seller AS jsonb),"
    " buyer = CAST(:buyer AS jsonb), finalized_at = now(), finalized_by = :u WHERE id = :i"
)
_TENANT_IDS = text("SELECT id FROM tenants ORDER BY created_at, id")

# ---------------------------------------------------------------- Plivo CDR + number rental
_PLIVO_ASSIGN_INSERT = text(
    "INSERT INTO plivo_number_assignments (plivo_number, tenant_id, assigned_from, assigned_to,"
    " monthly_rate_paise, created_by) VALUES (:num, :t, :af, :at, :rate, :u)"
    " RETURNING id, plivo_number, tenant_id, assigned_from, assigned_to, monthly_rate_paise,"
    " created_at"
)
_PLIVO_ASSIGNMENTS = text(
    "SELECT id, plivo_number, tenant_id, assigned_from, assigned_to, monthly_rate_paise,"
    " created_at FROM plivo_number_assignments"
    " WHERE (CAST(:t AS uuid) IS NULL OR tenant_id = CAST(:t AS uuid))"
    " ORDER BY plivo_number, assigned_from DESC"
)
_PLIVO_RENTAL_FOR_PERIOD = text(
    "SELECT DISTINCT ON (plivo_number) tenant_id, monthly_rate_paise FROM"
    " plivo_number_assignments WHERE assigned_from <= :period_end"
    " AND (assigned_to IS NULL OR assigned_to >= :period_start)"
    " ORDER BY plivo_number, assigned_from DESC"
)
_PLIVO_CALL_UPSERT = text(
    "INSERT INTO plivo_call_records (call_uuid, tenant_id, direction, from_number, to_number,"
    " duration_seconds, billed_seconds, call_status, provider_charge_inr, computed_cost_paise,"
    " billing_period, voice_call_id, raw, updated_at)"
    " VALUES (:call_uuid, :t, :direction, :from_number, :to_number, :duration, :billed,"
    " :status, :charge, :cost, :period, :voice_call_id, CAST(:raw AS jsonb), now())"
    " ON CONFLICT (call_uuid) DO UPDATE SET tenant_id = COALESCE(EXCLUDED.tenant_id,"
    " plivo_call_records.tenant_id), direction = EXCLUDED.direction,"
    " from_number = EXCLUDED.from_number, to_number = EXCLUDED.to_number,"
    " duration_seconds = EXCLUDED.duration_seconds, billed_seconds = EXCLUDED.billed_seconds,"
    " call_status = EXCLUDED.call_status, provider_charge_inr = EXCLUDED.provider_charge_inr,"
    " computed_cost_paise = EXCLUDED.computed_cost_paise, raw = EXCLUDED.raw, updated_at = now()"
    " RETURNING id, call_uuid, tenant_id, direction, from_number, to_number, duration_seconds,"
    " billed_seconds, call_status, computed_cost_paise, billing_period, created_at"
)
_PLIVO_CALLS_FOR_PERIOD = text(
    "SELECT tenant_id, COALESCE(sum(computed_cost_paise), 0) AS cost_paise FROM"
    " plivo_call_records WHERE billing_period = :p AND tenant_id IS NOT NULL GROUP BY tenant_id"
)
_PLIVO_CALLS_LIST = text(
    "SELECT id, call_uuid, tenant_id, direction, from_number, to_number, duration_seconds,"
    " billed_seconds, call_status, computed_cost_paise, billing_period, created_at"
    " FROM plivo_call_records WHERE billing_period = :p"
    " AND (CAST(:t AS uuid) IS NULL OR tenant_id = CAST(:t AS uuid))"
    " ORDER BY created_at DESC LIMIT 500"
)

# ---------------------------------------------------------------- STT/TTS per-call usage
_SPEECH_USAGE_INSERT = text(
    "INSERT INTO speech_usage_records (event_key, tenant_id, voice_call_id, provider, quantity,"
    " unit, rate_inr_snapshot, cost_paise, billing_period, created_by)"
    " VALUES (:key, :t, :call, :provider, :qty, :unit, :rate, :cost, :period, :u)"
    " ON CONFLICT (event_key) DO NOTHING"
    " RETURNING id, tenant_id, voice_call_id, provider, quantity, unit, cost_paise,"
    " billing_period, created_at"
)
_SPEECH_USAGE_FOR_PERIOD = text(
    "SELECT tenant_id, COALESCE(sum(cost_paise), 0) AS cost_paise FROM speech_usage_records"
    " WHERE billing_period = :p GROUP BY tenant_id"
)
_SPEECH_USAGE_LIST = text(
    "SELECT id, tenant_id, voice_call_id, provider, quantity, unit, cost_paise, billing_period,"
    " created_at FROM speech_usage_records WHERE billing_period = :p"
    " AND (CAST(:t AS uuid) IS NULL OR tenant_id = CAST(:t AS uuid))"
    " ORDER BY created_at DESC LIMIT 500"
)

# ---------------------------------------------------------------- GCP app-level metering
_GCP_EVENT_INSERT = text(
    "INSERT INTO gcp_usage_events (event_key, tenant_id, category, quantity, unit,"
    " usd_rate_snapshot, cost_usd, billing_period, created_by)"
    " VALUES (:key, :t, :category, :qty, :unit, :rate, :cost, :period, :u)"
    " ON CONFLICT (event_key) DO NOTHING"
    " RETURNING id, tenant_id, category, quantity, unit, cost_usd, billing_period, created_at"
)
_GCP_EVENTS_LIST = text(
    "SELECT id, tenant_id, category, quantity, unit, cost_usd, billing_period, created_at"
    " FROM gcp_usage_events WHERE billing_period = :p"
    " AND (CAST(:t AS uuid) IS NULL OR tenant_id = CAST(:t AS uuid)) ORDER BY created_at DESC"
    " LIMIT 1000"
)
_GCP_EVENTS_TOTAL_FOR_PERIOD = text(
    "SELECT COALESCE(sum(cost_usd), 0) FROM gcp_usage_events WHERE billing_period = :p"
)

# ---------------------------------------------------------------- GCP Billing Export import
_GCP_IMPORT_INSERT = text(
    "INSERT INTO gcp_billing_import_rows (import_batch_id, billing_period, billing_account,"
    " service, sku, usage_quantity, usage_unit, cost_usd, credits_usd, currency, resource_name,"
    " row_key, imported_by) VALUES (:batch, :period, :account, :service, :sku, :qty, :unit,"
    " :cost, :credits, :currency, :resource, :row_key, :u) ON CONFLICT (row_key) DO NOTHING"
    " RETURNING id"
)
_GCP_IMPORT_TOTAL_FOR_PERIOD = text(
    "SELECT COALESCE(sum(cost_usd - credits_usd), 0) FROM gcp_billing_import_rows"
    " WHERE billing_period = :p"
)
_GCP_RECON_INSERT = text(
    "INSERT INTO gcp_reconciliation_runs (billing_period, imported_total_usd, metered_total_usd,"
    " allocated_usd, unallocated_usd, created_by) VALUES (:p, :imported, :metered, :allocated,"
    " :unallocated, :u) RETURNING billing_period, imported_total_usd, metered_total_usd,"
    " allocated_usd, unallocated_usd, created_at"
)
_GCP_RECON_LATEST = text(
    "SELECT billing_period, imported_total_usd, metered_total_usd, allocated_usd,"
    " unallocated_usd, created_at FROM gcp_reconciliation_runs WHERE billing_period = :p"
    " ORDER BY created_at DESC LIMIT 1"
)
_GCP_UNALLOC_INSERT = text(
    "INSERT INTO gcp_unallocated_costs (billing_period, category, amount_usd, reason)"
    " VALUES (:p, :category, :amount, :reason) RETURNING id"
)
_GCP_UNALLOC_CLEAR = text(
    "DELETE FROM gcp_unallocated_costs WHERE billing_period = :p AND NOT resolved"
)
_GCP_UNALLOC_LIST = text(
    "SELECT id, billing_period, category, amount_usd, reason, resolved FROM gcp_unallocated_costs"
    " WHERE billing_period = :p ORDER BY resolved, amount_usd DESC"
)
_GCP_UNALLOC_RESOLVE = text(
    "UPDATE gcp_unallocated_costs SET resolved = :resolved,"
    " resolved_by = CASE WHEN :resolved THEN :u ELSE NULL END,"
    " resolved_at = CASE WHEN :resolved THEN now() ELSE NULL END WHERE id = :id RETURNING id"
)


# ---------------------------------------------------------------- settings, rates, profiles
async def load_settings(session: AsyncSession) -> BillingSettingsOut:
    row = (await session.execute(_SETTINGS)).first()
    if row is None:
        return BillingSettingsOut()
    return BillingSettingsOut(**row._mapping)


async def save_settings(
    session: AsyncSession, body: BillingSettingsIn, admin_id: uuid.UUID
) -> BillingSettingsOut:
    before = await load_settings(session)
    await session.execute(_SETTINGS_UPSERT, {**body.model_dump(), "updated_by": admin_id})
    after = await load_settings(session)
    changed = sorted(
        key for key, value in body.model_dump().items() if getattr(before, key) != value
    )
    await record(
        session,
        AuditEvent(
            action="admin.billing_settings_changed",
            entity_type="billing_settings",
            entity_id="1",
            new_value={"changed_fields": changed},
            actor_user_id=admin_id,
            actor_role="super_admin",
        ),
    )
    return after


async def load_rates(session: AsyncSession) -> LlmRatesOut:
    rows = (await session.execute(_RATES)).all()
    since = timeutil.utc_now() - timedelta(days=120)
    unpriced = (await session.execute(_UNPRICED, {"since": since})).all()
    return LlmRatesOut(
        rates=[LlmRateOut(**r._mapping) for r in rows],
        unpriced=[ModelRef(provider=r.provider, model=r.model) for r in unpriced],
    )


async def save_rate(session: AsyncSession, body: LlmRateIn, admin_id: uuid.UUID) -> LlmRateOut:
    row = (
        await session.execute(
            _RATE_UPSERT,
            {
                "provider": body.provider,
                "model": body.model.strip(),
                "i": body.input_usd_per_million,
                "o": body.output_usd_per_million,
                "u": admin_id,
            },
        )
    ).one()
    await record(
        session,
        AuditEvent(
            action="admin.billing_rate_changed",
            entity_type="billing_llm_rate",
            entity_id=f"{body.provider}/{body.model.strip()}",
            new_value={
                "input_usd_per_million": str(body.input_usd_per_million),
                "output_usd_per_million": str(body.output_usd_per_million),
            },
            actor_user_id=admin_id,
            actor_role="super_admin",
        ),
    )
    return LlmRateOut(
        provider=body.provider,
        model=body.model.strip(),
        input_usd_per_million=row.input_usd_per_million,
        output_usd_per_million=row.output_usd_per_million,
        updated_at=row.updated_at,
    )


async def load_speech_rates(session: AsyncSession) -> SpeechRatesOut:
    rows = (await session.execute(_SPEECH_RATES)).all()
    return SpeechRatesOut(rates=[SpeechRateOut(**r._mapping) for r in rows])


async def load_cost_categories(session: AsyncSession) -> CostCategoriesOut:
    rows = (await session.execute(_COST_CATEGORIES)).all()
    return CostCategoriesOut(categories=[CostCategoryOut(**r._mapping) for r in rows])


async def save_cost_category(
    session: AsyncSession, body: CostCategoryIn, admin_id: uuid.UUID
) -> CostCategoryOut:
    row = (
        await session.execute(
            _COST_CATEGORY_UPSERT,
            {
                "category": body.category.strip(),
                "unit": body.unit.strip(),
                "usd_rate": body.usd_rate,
                "attribution": body.attribution.strip(),
                "active": body.active,
                "u": admin_id,
            },
        )
    ).one()
    await record(
        session,
        AuditEvent(
            action="admin.billing_cost_category_changed",
            entity_type="billing_cost_category",
            entity_id=body.category.strip(),
            new_value={
                "unit": body.unit.strip(),
                "usd_rate": str(body.usd_rate),
                "attribution": body.attribution.strip(),
                "active": body.active,
            },
            actor_user_id=admin_id,
            actor_role="super_admin",
        ),
    )
    return CostCategoryOut(**row._mapping)


async def save_speech_rate(
    session: AsyncSession, body: SpeechRateIn, admin_id: uuid.UUID
) -> SpeechRateOut:
    row = (
        await session.execute(
            _SPEECH_RATE_UPSERT,
            {
                "provider": body.provider,
                "unit": body.unit,
                "rate": body.rate_inr,
                "active": body.active,
                "u": admin_id,
            },
        )
    ).one()
    await record(
        session,
        AuditEvent(
            action="admin.billing_speech_rate_changed",
            entity_type="billing_speech_rate",
            entity_id=body.provider,
            new_value={"unit": body.unit, "rate_inr": str(body.rate_inr), "active": body.active},
            actor_user_id=admin_id,
            actor_role="super_admin",
        ),
    )
    return SpeechRateOut(**row._mapping)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return cast(dict[str, Any], value)


def _profile_row(row: Any) -> ProfileRowOut:
    return ProfileRowOut(**row._mapping)


async def list_profiles(session: AsyncSession) -> list[ProfileRowOut]:
    rows = (await session.execute(_PROFILES, {"t": None})).all()
    return [_profile_row(r) for r in rows]


async def _one_profile(session: AsyncSession, tenant_id: uuid.UUID) -> ProfileRowOut:
    row = (await session.execute(_PROFILES, {"t": str(tenant_id)})).first()
    if row is None:
        raise AppError("E-NF-001")
    return _profile_row(row)


async def save_profile(
    session: AsyncSession, tenant_id: uuid.UUID, body: ProfileIn, admin_id: uuid.UUID
) -> ProfileRowOut:
    await _one_profile(session, tenant_id)
    await session.execute(_PROFILE_UPSERT, {**body.model_dump(), "t": tenant_id, "u": admin_id})
    await record(
        session,
        AuditEvent(
            action="admin.billing_profile_changed",
            entity_type="tenant",
            entity_id=str(tenant_id),
            new_value={"default_platform_fee_paise": body.default_platform_fee_paise},
            actor_user_id=admin_id,
            actor_role="super_admin",
        ),
        tenant_id=tenant_id,
    )
    return await _one_profile(session, tenant_id)


# ---------------------------------------------------------------- usage and pricing
@dataclass(frozen=True)
class LlmUsage:
    provider: str
    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    est_cost_micro: int
    unpriced_calls: int


@dataclass(frozen=True)
class LlmLine:
    provider: str
    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    usd: Decimal
    amount_paise: int
    source: Literal["rate", "call_log", "none", "missing"]
    rate_in: Decimal | None
    rate_out: Decimal | None


@dataclass(frozen=True)
class CloudRun:
    amount_paise: int
    basis: Literal["estimate", "actual"]
    milliseconds: int
    jobs: int
    share_percent: Decimal
    rate_missing: bool


@dataclass(frozen=True)
class Compute:
    milliseconds: int
    jobs: int


@dataclass(frozen=True)
class VoiceUsage:
    seconds: int
    calls: int


@dataclass(frozen=True)
class Telephony:
    amount_paise: int
    seconds: int
    calls: int
    share_percent: Decimal
    basis: TelephonyBasis = "none"
    rental_paise: int = 0


Rates = dict[tuple[str, str], tuple[Decimal, Decimal]]


def price_llm(usages: list[LlmUsage], rates: Rates, usd_inr: Decimal | None) -> list[LlmLine]:
    lines: list[LlmLine] = []
    for usage in sorted(usages, key=lambda u: (u.provider, u.model)):
        rate = rates.get((usage.provider, usage.model))
        rate_in = rate[0] if rate else None
        rate_out = rate[1] if rate else None
        usd = _ZERO
        source: Literal["rate", "call_log", "none", "missing"]
        if rate is not None:
            usd = calc.llm_cost_usd(usage.input_tokens, usage.output_tokens, rate[0], rate[1])
            source = "rate"
        elif usage.input_tokens == 0 and usage.output_tokens == 0:
            source = "none"
        elif usage.unpriced_calls == 0 and usage.est_cost_micro > 0:
            usd = Decimal(usage.est_cost_micro) / _MILLION
            source = "call_log"
        else:
            source = "missing"
        lines.append(
            LlmLine(
                provider=usage.provider,
                model=usage.model,
                calls=usage.calls,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                usd=usd,
                amount_paise=calc.usd_to_paise(usd, usd_inr) if usd_inr is not None else 0,
                source=source,
                rate_in=rate_in,
                rate_out=rate_out,
            )
        )
    return lines


def price_cloudrun(
    settings: BillingSettingsOut,
    usd_inr: Decimal | None,
    actual_paise: int | None,
    compute: dict[uuid.UUID, Compute],
) -> dict[uuid.UUID, CloudRun]:
    total_ms = sum(c.milliseconds for c in compute.values())
    per_second = (
        settings.cloudrun_vcpu * settings.cloudrun_usd_per_vcpu_second
        + settings.cloudrun_gib * settings.cloudrun_usd_per_gib_second
    )
    result: dict[uuid.UUID, CloudRun] = {}
    shares = (
        calc.allocate(actual_paise, {t: c.milliseconds for t, c in compute.items()})
        if actual_paise is not None
        else {}
    )
    for tenant_id, usage in compute.items():
        share = Decimal(usage.milliseconds) * 100 / Decimal(total_ms) if total_ms > 0 else _ZERO
        if actual_paise is not None:
            result[tenant_id] = CloudRun(
                shares[tenant_id], "actual", usage.milliseconds, usage.jobs, share, False
            )
            continue
        amount = (
            calc.usd_to_paise(
                calc.compute_cost_usd(
                    usage.milliseconds,
                    settings.cloudrun_vcpu,
                    settings.cloudrun_gib,
                    settings.cloudrun_usd_per_vcpu_second,
                    settings.cloudrun_usd_per_gib_second,
                ),
                usd_inr,
            )
            if usd_inr is not None
            else 0
        )
        result[tenant_id] = CloudRun(
            amount,
            "estimate",
            usage.milliseconds,
            usage.jobs,
            share,
            usage.milliseconds > 0 and per_second == 0,
        )
    return result


def price_telephony(
    actual_paise: int | None,
    voice: dict[uuid.UUID, VoiceUsage],
    settings: BillingSettingsOut,
    cdr_cost_by_tenant: dict[uuid.UUID, int],
    rental_cost_by_tenant: dict[uuid.UUID, int],
) -> dict[uuid.UUID, Telephony]:
    """Plivo/telephony cost, three tiers in priority order:
    1. actual_paise - admin entered the real monthly Plivo bill; allocate by call-seconds
       (most accurate, overrides everything else once entered).
    2. cdr_cost_by_tenant - sum of plivo_call_records.computed_cost_paise for the period
       (real per-call CDR cost, computed from the configured per-minute rates).
    3. estimate - no CDR ingested yet, so estimate from voice_calls seconds x configured
       inbound rate (the agent is inbound-only, so this is treated as inbound minutes).
    Phone number rental (rental_cost_by_tenant) is added on top of whichever tier applies,
    since rental is owed regardless of call volume."""
    total_seconds = sum(v.seconds for v in voice.values())
    cdr_total = sum(cdr_cost_by_tenant.values())
    if actual_paise is not None:
        shares = calc.allocate(actual_paise, {t: v.seconds for t, v in voice.items()})
        basis: TelephonyBasis = "actual"
    elif cdr_total > 0:
        shares = dict(cdr_cost_by_tenant)
        basis = "cdr_computed"
    elif settings.plivo_inbound_inr_per_min > 0:
        per_second = settings.plivo_inbound_inr_per_min / Decimal(60)
        shares = {
            t: calc.round_paise(Decimal(usage.seconds) * per_second) for t, usage in voice.items()
        }
        basis = "estimate"
    else:
        shares = {}
        basis = "none"
    result: dict[uuid.UUID, Telephony] = {}
    tenants = set(voice) | set(rental_cost_by_tenant)
    for tenant_id in tenants:
        usage = voice.get(tenant_id, VoiceUsage(0, 0))
        share = (
            Decimal(usage.seconds) * 100 / Decimal(total_seconds) if total_seconds > 0 else _ZERO
        )
        rental = rental_cost_by_tenant.get(tenant_id, 0)
        calling = shares.get(tenant_id, 0)
        result[tenant_id] = Telephony(
            calling + rental, usage.seconds, usage.calls, share, basis, rental
        )
    return result


@dataclass(frozen=True)
class PeriodData:
    period_start: date
    usd_inr: Decimal | None
    actual_paise: int | None
    telephony_actual_paise: int | None
    settings: BillingSettingsOut
    rates: Rates
    llm: dict[uuid.UUID, list[LlmUsage]]
    compute: dict[uuid.UUID, Compute]
    voice: dict[uuid.UUID, VoiceUsage]
    plivo_cdr_cost: dict[uuid.UUID, int]
    plivo_rental_cost: dict[uuid.UUID, int]
    speech_cost: dict[uuid.UUID, int]


async def _plivo_rental_cost(session: AsyncSession, period_start: date) -> dict[uuid.UUID, int]:
    _, end = calc.period_bounds(period_start)
    period_end = end - timedelta(days=1)
    rows = (
        await session.execute(
            _PLIVO_RENTAL_FOR_PERIOD, {"period_start": period_start, "period_end": period_end}
        )
    ).all()
    result: dict[uuid.UUID, int] = {}
    for r in rows:
        result[r.tenant_id] = result.get(r.tenant_id, 0) + int(r.monthly_rate_paise)
    return result


async def _collect(
    session: AsyncSession, period_start: date, llm_tenant: uuid.UUID | None
) -> PeriodData:
    start, end = calc.period_bounds(period_start)
    period = (await session.execute(_PERIOD, {"p": period_start})).first()
    rate_rows = (await session.execute(_RATES)).all()
    llm_rows = (
        await session.execute(
            _LLM_USAGE,
            {"s": start, "e": end, "t": str(llm_tenant) if llm_tenant else None},
        )
    ).all()
    compute_rows = (await session.execute(_COMPUTE_USAGE, {"s": start, "e": end})).all()
    voice_rows = (await session.execute(_VOICE_USAGE, {"s": start, "e": end})).all()
    plivo_rows = (await session.execute(_PLIVO_CALLS_FOR_PERIOD, {"p": period_start})).all()
    speech_rows = (await session.execute(_SPEECH_USAGE_FOR_PERIOD, {"p": period_start})).all()
    rental_cost = await _plivo_rental_cost(session, period_start)
    llm: dict[uuid.UUID, list[LlmUsage]] = {}
    for r in llm_rows:
        llm.setdefault(r.tenant_id, []).append(
            LlmUsage(
                r.provider,
                r.model,
                int(r.calls),
                int(r.input_tokens),
                int(r.output_tokens),
                int(r.est_cost_micro),
                int(r.unpriced_calls),
            )
        )
    return PeriodData(
        period_start=period_start,
        usd_inr=period.usd_inr if period else None,
        actual_paise=int(period.cloudrun_actual_paise)
        if period and period.cloudrun_actual_paise is not None
        else None,
        telephony_actual_paise=int(period.telephony_actual_paise)
        if period and period.telephony_actual_paise is not None
        else None,
        settings=await load_settings(session),
        rates={
            (r.provider, r.model): (r.input_usd_per_million, r.output_usd_per_million)
            for r in rate_rows
        },
        llm=llm,
        compute={r.tenant_id: Compute(int(r.ms), int(r.jobs)) for r in compute_rows},
        voice={r.tenant_id: VoiceUsage(int(r.seconds), int(r.calls)) for r in voice_rows},
        plivo_cdr_cost={r.tenant_id: int(r.cost_paise) for r in plivo_rows},
        plivo_rental_cost=rental_cost,
        speech_cost={r.tenant_id: int(r.cost_paise) for r in speech_rows},
    )


def _period_ended(period_start: date) -> bool:
    return calc.period_bounds(period_start)[1] <= timeutil.utc_now()


def _check_period(period_start: date) -> None:
    current = timeutil.to_ist(timeutil.utc_now()).date().replace(day=1)
    if period_start > current:
        raise AppError("E-VAL-001", detail="period is in the future")


def _today() -> date:
    return timeutil.to_ist(timeutil.utc_now()).date()


def _pay_status(
    status: Literal["draft", "final"],
    due_date: date | None,
    paid_at: Any,
    cancelled_at: Any = None,
    paid_amount_paise: int = 0,
    total_paise: int = 0,
) -> PaymentStatus:
    return calc.payment_status(
        status,
        due_date,
        paid_at,
        _today(),
        cancelled=cancelled_at is not None,
        paid_amount_paise=paid_amount_paise,
        total_paise=total_paise,
    )


# ---------------------------------------------------------------- period overview
async def save_period(
    session: AsyncSession, period_start: date, body: PeriodIn, admin_id: uuid.UUID
) -> PeriodOut:
    _check_period(period_start)
    existing = (await session.execute(_PERIOD, {"p": period_start})).first()
    period_invoices = (await session.execute(_INVOICES_FOR_PERIOD, {"p": period_start})).all()
    if any(inv.status == "final" for inv in period_invoices):
        raise AppError("E-CONF-001")
    await session.execute(
        _PERIOD_UPSERT,
        {
            "p": period_start,
            "fx": body.usd_inr,
            "actual": body.cloudrun_actual_paise,
            "tel_actual": body.telephony_actual_paise,
            "u": admin_id,
        },
    )
    await record(
        session,
        AuditEvent(
            action="admin.billing_period_changed",
            entity_type="billing_period",
            entity_id=calc.period_label(period_start),
            old_value={
                "usd_inr": str(existing.usd_inr) if existing and existing.usd_inr else None,
                "cloudrun_actual_paise": existing.cloudrun_actual_paise if existing else None,
                "telephony_actual_paise": existing.telephony_actual_paise if existing else None,
            },
            new_value={
                "usd_inr": str(body.usd_inr) if body.usd_inr else None,
                "cloudrun_actual_paise": body.cloudrun_actual_paise,
                "telephony_actual_paise": body.telephony_actual_paise,
            },
            actor_user_id=admin_id,
            actor_role="super_admin",
        ),
    )
    return await period_overview(session, period_start)


async def period_overview(session: AsyncSession, period_start: date) -> PeriodOut:
    data = await _collect(session, period_start, None)
    cloud = price_cloudrun(data.settings, data.usd_inr, data.actual_paise, data.compute)
    telephony = price_telephony(
        data.telephony_actual_paise,
        data.voice,
        data.settings,
        data.plivo_cdr_cost,
        data.plivo_rental_cost,
    )
    profiles = await list_profiles(session)
    invoices = {
        r.tenant_id: InvoiceSummaryOut(
            id=r.id,
            tenant_id=r.tenant_id,
            status=r.status,
            payment_status=_pay_status(
                r.status,
                r.due_date,
                r.paid_at,
                r.cancelled_at,
                int(r.paid_amount_paise),
                int(r.total_paise),
            ),
            invoice_no=r.invoice_no,
            due_date=r.due_date,
            total_paise=int(r.total_paise),
            paid_amount_paise=int(r.paid_amount_paise),
        )
        for r in (await session.execute(_INVOICES_FOR_PERIOD, {"p": period_start})).all()
    }
    cloud_known = data.actual_paise is not None or data.usd_inr is not None
    rows: list[PeriodRowOut] = []
    for profile in profiles:
        usages = data.llm.get(profile.tenant_id, [])
        lines = price_llm(usages, data.rates, data.usd_inr)
        compute = cloud.get(profile.tenant_id)
        voice = telephony.get(profile.tenant_id)
        rows.append(
            PeriodRowOut(
                tenant_id=profile.tenant_id,
                name=profile.tenant_name,
                slug=profile.slug,
                calls=sum(u.calls for u in usages),
                input_tokens=sum(u.input_tokens for u in usages),
                output_tokens=sum(u.output_tokens for u in usages),
                llm_paise=sum(line.amount_paise for line in lines)
                if data.usd_inr is not None
                else None,
                compute_seconds=(compute.milliseconds // 1000) if compute else 0,
                jobs=compute.jobs if compute else 0,
                cloudrun_paise=(compute.amount_paise if compute else 0) if cloud_known else None,
                voice_calls=voice.calls if voice else 0,
                voice_call_seconds=voice.seconds if voice else 0,
                telephony_paise=(voice.amount_paise if voice else 0),
                telephony_basis=voice.basis if voice else "none",
                number_rental_paise=voice.rental_paise if voice else 0,
                speech_paise=data.speech_cost.get(profile.tenant_id, 0),
                default_platform_fee_paise=profile.default_platform_fee_paise,
                unpriced_models=[
                    f"{line.provider}/{line.model}" for line in lines if line.source == "missing"
                ],
                invoice=invoices.get(profile.tenant_id),
            )
        )
    allocated = sum(c.amount_paise for c in cloud.values()) if data.actual_paise is not None else 0
    tel_allocated = (
        sum(v.amount_paise for v in telephony.values())
        if data.telephony_actual_paise is not None
        else 0
    )
    return PeriodOut(
        period=calc.period_label(period_start),
        period_ended=_period_ended(period_start),
        usd_inr=data.usd_inr,
        cloudrun_actual_paise=data.actual_paise,
        cloudrun_unallocated_paise=(data.actual_paise - allocated)
        if data.actual_paise is not None
        else 0,
        telephony_actual_paise=data.telephony_actual_paise,
        telephony_unallocated_paise=(data.telephony_actual_paise - tel_allocated)
        if data.telephony_actual_paise is not None
        else 0,
        rows=rows,
    )


# ---------------------------------------------------------------- invoice building
@dataclass(frozen=True)
class BuiltLine:
    kind: Literal["llm", "cloudrun", "telephony", "speech", "platform_fee"]
    description: str
    quantity: Decimal
    unit: str
    amount_paise: int
    detail: dict[str, Any]


@dataclass(frozen=True)
class BuiltInvoice:
    tenant_id: uuid.UUID
    usd_inr: Decimal
    basis: Literal["estimate", "actual"]
    llm_paise: int
    cloudrun_paise: int
    telephony_paise: int
    speech_paise: int
    platform_fee_paise: int
    gst_rate_percent: Decimal
    gst: calc.GstSplit
    lines: list[BuiltLine]

    @property
    def subtotal_paise(self) -> int:
        return (
            self.llm_paise
            + self.cloudrun_paise
            + self.telephony_paise
            + self.speech_paise
            + self.platform_fee_paise
        )

    @property
    def total_paise(self) -> int:
        return self.subtotal_paise + self.gst.total_paise


def build_invoice(
    tenant_id: uuid.UUID,
    data: PeriodData,
    usd_inr: Decimal,
    cloud: CloudRun | None,
    telephony: Telephony | None,
    platform_fee_paise: int,
    buyer_state: str,
) -> BuiltInvoice:
    llm_lines = price_llm(data.llm.get(tenant_id, []), data.rates, usd_inr)
    lines: list[BuiltLine] = []
    for line in llm_lines:
        lines.append(
            BuiltLine(
                "llm",
                f"LLM usage - {line.provider}/{line.model}",
                Decimal(line.calls),
                "calls",
                line.amount_paise,
                {
                    "provider": line.provider,
                    "model": line.model,
                    "calls": line.calls,
                    "input_tokens": line.input_tokens,
                    "output_tokens": line.output_tokens,
                    "usd": str(line.usd),
                    "source": line.source,
                    "rate_in": str(line.rate_in) if line.rate_in is not None else None,
                    "rate_out": str(line.rate_out) if line.rate_out is not None else None,
                },
            )
        )
    llm_paise = sum(line.amount_paise for line in llm_lines)
    cloud_paise = cloud.amount_paise if cloud else 0
    if cloud and (cloud.milliseconds > 0 or cloud.amount_paise > 0):
        lines.append(
            BuiltLine(
                "cloudrun",
                "Cloud Run compute (agent processing time)",
                (Decimal(cloud.milliseconds) / Decimal(1000) / _SECONDS_PER_HOUR).quantize(
                    Decimal("0.0001")
                ),
                "hours",
                cloud.amount_paise,
                {
                    "basis": cloud.basis,
                    "seconds": cloud.milliseconds // 1000,
                    "jobs": cloud.jobs,
                    "share_percent": str(cloud.share_percent.quantize(Decimal("0.01"))),
                    "rate_missing": cloud.rate_missing,
                },
            )
        )
    telephony_paise = telephony.amount_paise if telephony else 0
    if telephony and (telephony.seconds > 0 or telephony.amount_paise > 0):
        calling_paise = telephony.amount_paise - telephony.rental_paise
        lines.append(
            BuiltLine(
                "telephony",
                "Telephony (Plivo calling minutes)",
                (Decimal(telephony.seconds) / Decimal(60)).quantize(Decimal("0.01")),
                "minutes",
                calling_paise,
                {
                    "seconds": telephony.seconds,
                    "calls": telephony.calls,
                    "share_percent": str(telephony.share_percent.quantize(Decimal("0.01"))),
                    "basis": telephony.basis,
                },
            )
        )
    if telephony and telephony.rental_paise > 0:
        lines.append(
            BuiltLine(
                "telephony",
                "Plivo phone number rental",
                Decimal(1),
                "month",
                telephony.rental_paise,
                {"basis": "rental"},
            )
        )
    speech_paise = data.speech_cost.get(tenant_id, 0)
    if speech_paise > 0:
        lines.append(
            BuiltLine(
                "speech",
                "STT / TTS usage (Sarvam / Google speech)",
                Decimal(1),
                "period",
                speech_paise,
                {"basis": "per_call_metered"},
            )
        )
    if platform_fee_paise > 0:
        lines.append(
            BuiltLine(
                "platform_fee",
                "Platform fee - AI support agent",
                Decimal(1),
                "month",
                platform_fee_paise,
                {},
            )
        )
    subtotal = llm_paise + cloud_paise + telephony_paise + speech_paise + platform_fee_paise
    gst = calc.gst_split(
        subtotal,
        data.settings.gst_rate_percent,
        data.settings.gst_enabled,
        data.settings.seller_state_code,
        buyer_state,
    )
    return BuiltInvoice(
        tenant_id=tenant_id,
        usd_inr=usd_inr,
        basis=cloud.basis if cloud else ("actual" if data.actual_paise is not None else "estimate"),
        llm_paise=llm_paise,
        cloudrun_paise=cloud_paise,
        telephony_paise=telephony_paise,
        speech_paise=speech_paise,
        platform_fee_paise=platform_fee_paise,
        gst_rate_percent=data.settings.gst_rate_percent if gst.kind != "none" else _ZERO,
        gst=gst,
        lines=lines,
    )


async def _save_draft(session: AsyncSession, period_start: date, built: BuiltInvoice) -> uuid.UUID:
    row = (
        await session.execute(
            _INVOICE_UPSERT,
            {
                "t": built.tenant_id,
                "p": period_start,
                "fx": built.usd_inr,
                "basis": built.basis,
                "llm": built.llm_paise,
                "cloud": built.cloudrun_paise,
                "tel": built.telephony_paise,
                "speech": built.speech_paise,
                "fee": built.platform_fee_paise,
                "sub": built.subtotal_paise,
                "rate": built.gst_rate_percent,
                "kind": built.gst.kind,
                "cgst": built.gst.cgst_paise,
                "sgst": built.gst.sgst_paise,
                "igst": built.gst.igst_paise,
                "total": built.total_paise,
            },
        )
    ).one()
    invoice_id = cast(uuid.UUID, row.id)
    await session.execute(_LINES_DELETE, {"i": invoice_id})
    for position, line in enumerate(built.lines, start=1):
        await session.execute(
            _LINE_INSERT,
            {
                "t": built.tenant_id,
                "i": invoice_id,
                "pos": position,
                "kind": line.kind,
                "descr": line.description,
                "qty": line.quantity,
                "unit": line.unit,
                "amount": line.amount_paise,
                "detail": json.dumps(line.detail, ensure_ascii=False),
            },
        )
    return invoice_id


async def _rebuild(
    session: AsyncSession,
    data: PeriodData,
    tenant_id: uuid.UUID,
    fee_override: int | None,
) -> tuple[uuid.UUID, BuiltInvoice] | None:
    if data.usd_inr is None:
        raise AppError("E-VAL-001", detail="usd_inr not set for this period")
    existing = (
        await session.execute(_INVOICE_LOCK, {"t": tenant_id, "p": data.period_start})
    ).first()
    if existing is not None and existing.status == "final":
        return None
    profile = await _one_profile(session, tenant_id)
    fee = (
        fee_override
        if fee_override is not None
        else int(existing.platform_fee_paise)
        if existing is not None
        else profile.default_platform_fee_paise
    )
    cloud_all = price_cloudrun(data.settings, data.usd_inr, data.actual_paise, data.compute)
    telephony_all = price_telephony(
        data.telephony_actual_paise,
        data.voice,
        data.settings,
        data.plivo_cdr_cost,
        data.plivo_rental_cost,
    )
    built = build_invoice(
        tenant_id,
        data,
        data.usd_inr,
        cloud_all.get(tenant_id),
        telephony_all.get(tenant_id),
        fee,
        profile.state_code,
    )
    invoice_id = await _save_draft(session, data.period_start, built)
    return invoice_id, built


async def generate(
    session: AsyncSession,
    period_start: date,
    tenant_id: uuid.UUID | None,
    admin_id: uuid.UUID,
) -> GenerateOut:
    _check_period(period_start)
    data = await _collect(session, period_start, None)
    if tenant_id is not None:
        await _one_profile(session, tenant_id)
        targets = [tenant_id]
    else:
        ids = [r.id for r in (await session.execute(_TENANT_IDS)).all()]
        profiles = {p.tenant_id: p for p in await list_profiles(session)}
        targets = [
            t
            for t in ids
            if data.llm.get(t)
            or t in data.compute
            or t in data.voice
            or profiles[t].default_platform_fee_paise > 0
        ]
    generated = 0
    skipped = 0
    summaries: list[InvoiceSummaryOut] = []
    for target in targets:
        result = await _rebuild(session, data, target, None)
        if result is None:
            skipped += 1
            if tenant_id is not None:
                raise AppError("E-CONF-001")
            continue
        invoice_id, built = result
        generated += 1
        summaries.append(
            InvoiceSummaryOut(
                id=invoice_id,
                tenant_id=target,
                status="draft",
                payment_status="draft",
                invoice_no=None,
                due_date=None,
                total_paise=built.total_paise,
            )
        )
        await record(
            session,
            AuditEvent(
                action="admin.invoice_generated",
                entity_type="invoice",
                entity_id=str(invoice_id),
                new_value={
                    "period": calc.period_label(period_start),
                    "total_paise": built.total_paise,
                },
                actor_user_id=admin_id,
                actor_role="super_admin",
            ),
            tenant_id=target,
        )
    return GenerateOut(generated=generated, skipped_final=skipped, invoices=summaries)


# ---------------------------------------------------------------- reading an invoice
def _seller(settings: BillingSettingsOut) -> SellerOut:
    return SellerOut(
        name=settings.seller_name,
        address=settings.seller_address,
        state_code=settings.seller_state_code,
        gstin=settings.seller_gstin,
        email=settings.seller_email,
        phone=settings.seller_phone,
        payment_details=settings.payment_details,
        sac_code=settings.sac_code,
    )


def _buyer(profile: ProfileRowOut) -> BuyerOut:
    return BuyerOut(
        name=profile.legal_name or profile.tenant_name,
        address=profile.address,
        state_code=profile.state_code,
        gstin=profile.gstin,
        email=profile.email,
    )


def _flags(
    row: Any,
    lines: list[InvoiceLineOut],
    settings: BillingSettingsOut,
    buyer: BuyerOut,
) -> tuple[list[FlagOut], list[FlagOut]]:
    blockers: list[FlagOut] = []
    warnings: list[FlagOut] = []
    if not _period_ended(row.period_start):
        blockers.append(FlagOut(code="period_running"))
    for line in lines:
        if line.kind == "llm" and line.detail.get("source") == "missing":
            blockers.append(
                FlagOut(
                    code="llm_rate_missing",
                    detail=f"{line.detail.get('provider')}/{line.detail.get('model')}",
                )
            )
        if line.kind == "cloudrun" and line.detail.get("rate_missing"):
            blockers.append(FlagOut(code="cloudrun_rate_missing"))
    if not settings.seller_name or not settings.seller_address:
        blockers.append(FlagOut(code="seller_incomplete"))
    if settings.gst_enabled and not settings.seller_state_code:
        blockers.append(FlagOut(code="seller_state_missing"))
    if settings.gst_enabled and not buyer.state_code:
        blockers.append(FlagOut(code="buyer_state_missing"))
    if row.total_paise <= 0:
        blockers.append(FlagOut(code="empty_invoice"))
    if settings.gst_enabled and not settings.seller_gstin:
        warnings.append(FlagOut(code="gst_without_gstin"))
    if not buyer.address:
        warnings.append(FlagOut(code="buyer_address_missing"))
    if row.platform_fee_paise <= 0:
        warnings.append(FlagOut(code="platform_fee_zero"))
    return blockers, warnings


async def get_invoice(session: AsyncSession, invoice_id: uuid.UUID) -> InvoiceOut:
    row = (await session.execute(_INVOICE, {"i": invoice_id})).first()
    if row is None:
        raise AppError("E-NF-001")
    lines = [
        InvoiceLineOut(
            position=r.position,
            kind=r.kind,
            description=r.description,
            quantity=r.quantity,
            unit=r.unit,
            amount_paise=int(r.amount_paise),
            detail=_as_dict(r.detail),
        )
        for r in (await session.execute(_LINES, {"i": invoice_id})).all()
    ]
    settings = await load_settings(session)
    payments = [
        InvoicePaymentOut(**p._mapping)
        for p in (await session.execute(_PAYMENTS_FOR_INVOICE, {"i": invoice_id})).all()
    ]
    if row.status == "final":
        seller = SellerOut(**_as_dict(row.seller))
        buyer = BuyerOut(**_as_dict(row.buyer))
        blockers: list[FlagOut] = []
        warnings: list[FlagOut] = []
    else:
        seller = _seller(settings)
        buyer = _buyer(await _one_profile(session, row.tenant_id))
        blockers, warnings = _flags(row, lines, settings, buyer)
    paid_amount_paise = int(row.paid_amount_paise)
    total_paise = int(row.total_paise)
    return InvoiceOut(
        id=row.id,
        tenant_id=row.tenant_id,
        tenant_name=row.tenant_name,
        period=calc.period_label(row.period_start),
        period_start=row.period_start,
        status=row.status,
        payment_status=_pay_status(
            row.status, row.due_date, row.paid_at, row.cancelled_at, paid_amount_paise, total_paise
        ),
        doc_type=row.doc_type
        if row.status == "final"
        else ("tax_invoice" if settings.seller_gstin else "proforma"),
        invoice_no=row.invoice_no,
        invoice_date=row.invoice_date,
        due_date=row.due_date,
        paid_at=row.paid_at,
        paid_amount_paise=paid_amount_paise,
        balance_due_paise=max(total_paise - paid_amount_paise, 0),
        cancelled_at=row.cancelled_at,
        cancelled_reason=row.cancelled_reason or "",
        payments=payments,
        usd_inr=row.usd_inr,
        cloudrun_basis=row.cloudrun_basis,
        llm_paise=int(row.llm_paise),
        cloudrun_paise=int(row.cloudrun_paise),
        telephony_paise=int(row.telephony_paise),
        speech_paise=int(row.speech_paise),
        platform_fee_paise=int(row.platform_fee_paise),
        subtotal_paise=int(row.subtotal_paise),
        gst_rate_percent=row.gst_rate_percent,
        gst_kind=row.gst_kind,
        cgst_paise=int(row.cgst_paise),
        sgst_paise=int(row.sgst_paise),
        igst_paise=int(row.igst_paise),
        total_paise=int(row.total_paise),
        seller=seller,
        buyer=buyer,
        lines=lines,
        blockers=blockers,
        warnings=warnings,
        generated_at=row.generated_at,
        finalized_at=row.finalized_at,
    )


# ---------------------------------------------------------------- draft edit and finalize
async def set_fee(
    session: AsyncSession, invoice_id: uuid.UUID, platform_fee_paise: int, admin_id: uuid.UUID
) -> InvoiceOut:
    row = (await session.execute(_INVOICE_LOCKED, {"i": invoice_id})).first()
    if row is None:
        raise AppError("E-NF-001")
    if row.status == "final":
        raise AppError("E-CONF-001")
    data = await _collect(session, row.period_start, None)
    result = await _rebuild(session, data, row.tenant_id, platform_fee_paise)
    if result is None:
        raise AppError("E-CONF-001")
    await record(
        session,
        AuditEvent(
            action="admin.invoice_fee_changed",
            entity_type="invoice",
            entity_id=str(invoice_id),
            old_value={"platform_fee_paise": int(row.platform_fee_paise)},
            new_value={"platform_fee_paise": platform_fee_paise},
            actor_user_id=admin_id,
            actor_role="super_admin",
        ),
        tenant_id=row.tenant_id,
    )
    return await get_invoice(session, invoice_id)


async def finalize(
    session: AsyncSession, invoice_id: uuid.UUID, expected_total_paise: int, admin_id: uuid.UUID
) -> InvoiceOut:
    row = (await session.execute(_INVOICE_LOCKED, {"i": invoice_id})).first()
    if row is None:
        raise AppError("E-NF-001")
    if row.status == "final":
        raise AppError("E-CONF-001")
    data = await _collect(session, row.period_start, None)
    result = await _rebuild(session, data, row.tenant_id, None)
    if result is None:
        raise AppError("E-CONF-001")
    _, built = result
    if built.total_paise != expected_total_paise:
        raise AppError("E-CONF-001", detail="totals changed since the draft was shown")
    draft = await get_invoice(session, invoice_id)
    if draft.blockers:
        raise AppError("E-VAL-001", detail=",".join(b.code for b in draft.blockers))
    today = timeutil.to_ist(timeutil.utc_now()).date()
    fy = calc.fiscal_year(today)
    number = int((await session.execute(_COUNTER, {"fy": fy})).scalar_one())
    invoice_no = calc.format_invoice_no(data.settings.invoice_prefix, fy, number)
    profile = await _one_profile(session, row.tenant_id)
    due_date = today + timedelta(days=profile.payment_due_days)
    await session.execute(
        _FINALIZE,
        {
            "doc": draft.doc_type,
            "no": invoice_no,
            "on": today,
            "due": due_date,
            "seller": draft.seller.model_dump_json(),
            "buyer": draft.buyer.model_dump_json(),
            "u": admin_id,
            "i": invoice_id,
        },
    )
    await record(
        session,
        AuditEvent(
            action="admin.invoice_finalized",
            entity_type="invoice",
            entity_id=str(invoice_id),
            new_value={
                "invoice_no": invoice_no,
                "period": draft.period,
                "total_paise": built.total_paise,
            },
            actor_user_id=admin_id,
            actor_role="super_admin",
        ),
        tenant_id=row.tenant_id,
    )
    return await get_invoice(session, invoice_id)


async def _invoice_tenant_and_total(session: AsyncSession, invoice_id: uuid.UUID) -> Any:
    row = (
        await session.execute(
            text("SELECT tenant_id, status, total_paise FROM invoices WHERE id = :i"),
            {"i": invoice_id},
        )
    ).first()
    if row is None:
        raise AppError("E-NF-001")
    return row


async def _paid_sum(session: AsyncSession, invoice_id: uuid.UUID) -> int:
    return int(
        (
            await session.execute(
                text(
                    "SELECT COALESCE(sum(amount_paise), 0) FROM invoice_payments"
                    " WHERE invoice_id = :i"
                ),
                {"i": invoice_id},
            )
        ).scalar_one()
    )


async def mark_paid(
    session: AsyncSession, invoice_id: uuid.UUID, paid: bool, admin_id: uuid.UUID
) -> InvoiceOut:
    """Convenience toggle used by the simple Mark paid/unpaid button: fully settles (or clears)
    the invoice via the payments ledger so partial-payment totals stay consistent."""
    info = await _invoice_tenant_and_total(session, invoice_id)
    if info.status != "final":
        raise AppError("E-CONF-001", detail="invoice must be finalized first")
    if paid:
        remaining = int(info.total_paise) - await _paid_sum(session, invoice_id)
        if remaining > 0:
            await session.execute(
                _PAYMENT_INSERT,
                {
                    "t": info.tenant_id,
                    "i": invoice_id,
                    "amount": remaining,
                    "paid_on": _today(),
                    "method": "manual",
                    "note": "marked paid (full)",
                    "u": admin_id,
                },
            )
    else:
        await session.execute(_PAYMENTS_DELETE, {"i": invoice_id})
    row = (
        await session.execute(_MARK_PAID, {"i": invoice_id, "paid": paid, "u": admin_id})
    ).first()
    if row is None:
        raise AppError("E-NF-001")
    await record(
        session,
        AuditEvent(
            action="admin.invoice_paid_changed",
            entity_type="invoice",
            entity_id=str(invoice_id),
            new_value={"paid": paid},
            actor_user_id=admin_id,
            actor_role="super_admin",
        ),
        tenant_id=row.tenant_id,
    )
    return await get_invoice(session, invoice_id)


async def record_payment(
    session: AsyncSession, invoice_id: uuid.UUID, body: RecordPaymentIn, admin_id: uuid.UUID
) -> InvoiceOut:
    info = await _invoice_tenant_and_total(session, invoice_id)
    if info.status != "final":
        raise AppError("E-CONF-001", detail="invoice must be finalized first")
    already_paid = await _paid_sum(session, invoice_id)
    if already_paid + body.amount_paise > int(info.total_paise):
        raise AppError("E-VAL-001", detail="payment would exceed the invoice total")
    await session.execute(
        _PAYMENT_INSERT,
        {
            "t": info.tenant_id,
            "i": invoice_id,
            "amount": body.amount_paise,
            "paid_on": body.paid_on,
            "method": body.method,
            "note": body.note,
            "u": admin_id,
        },
    )
    new_sum = already_paid + body.amount_paise
    await session.execute(
        _MARK_PAID, {"i": invoice_id, "paid": new_sum >= int(info.total_paise), "u": admin_id}
    )
    await record(
        session,
        AuditEvent(
            action="admin.invoice_payment_recorded",
            entity_type="invoice",
            entity_id=str(invoice_id),
            new_value={"amount_paise": body.amount_paise, "paid_on": str(body.paid_on)},
            actor_user_id=admin_id,
            actor_role="super_admin",
        ),
        tenant_id=info.tenant_id,
    )
    return await get_invoice(session, invoice_id)


async def delete_payment(
    session: AsyncSession, invoice_id: uuid.UUID, payment_id: uuid.UUID, admin_id: uuid.UUID
) -> InvoiceOut:
    row = (await session.execute(_PAYMENT_DELETE_ONE, {"pid": payment_id, "i": invoice_id})).first()
    if row is None:
        raise AppError("E-NF-001")
    info = await _invoice_tenant_and_total(session, invoice_id)
    new_sum = await _paid_sum(session, invoice_id)
    await session.execute(
        _MARK_PAID,
        {"i": invoice_id, "paid": new_sum >= int(info.total_paise) and new_sum > 0, "u": admin_id},
    )
    await record(
        session,
        AuditEvent(
            action="admin.invoice_payment_deleted",
            entity_type="invoice",
            entity_id=str(invoice_id),
            new_value={"payment_id": str(payment_id)},
            actor_user_id=admin_id,
            actor_role="super_admin",
        ),
        tenant_id=row.tenant_id,
    )
    return await get_invoice(session, invoice_id)


async def cancel_invoice(
    session: AsyncSession, invoice_id: uuid.UUID, body: CancelInvoiceIn, admin_id: uuid.UUID
) -> InvoiceOut:
    row = (
        await session.execute(
            _CANCEL_INVOICE, {"i": invoice_id, "u": admin_id, "reason": body.reason}
        )
    ).first()
    if row is None:
        raise AppError("E-CONF-001", detail="invoice must be finalized and not already cancelled")
    await record(
        session,
        AuditEvent(
            action="admin.invoice_cancelled",
            entity_type="invoice",
            entity_id=str(invoice_id),
            new_value={"reason": body.reason},
            actor_user_id=admin_id,
            actor_role="super_admin",
        ),
        tenant_id=row.tenant_id,
    )
    return await get_invoice(session, invoice_id)


async def uncancel_invoice(
    session: AsyncSession, invoice_id: uuid.UUID, admin_id: uuid.UUID
) -> InvoiceOut:
    row = (await session.execute(_UNCANCEL_INVOICE, {"i": invoice_id})).first()
    if row is None:
        raise AppError("E-NF-001")
    await record(
        session,
        AuditEvent(
            action="admin.invoice_uncancelled",
            entity_type="invoice",
            entity_id=str(invoice_id),
            new_value={},
            actor_user_id=admin_id,
            actor_role="super_admin",
        ),
        tenant_id=row.tenant_id,
    )
    return await get_invoice(session, invoice_id)


# ---------------------------------------------------------------- Plivo CDR + number rental
async def save_plivo_assignment(
    session: AsyncSession, body: PlivoNumberAssignmentIn, admin_id: uuid.UUID
) -> PlivoNumberAssignmentOut:
    row = (
        await session.execute(
            _PLIVO_ASSIGN_INSERT,
            {
                "num": body.plivo_number.strip(),
                "t": body.tenant_id,
                "af": body.assigned_from,
                "at": body.assigned_to,
                "rate": body.monthly_rate_paise,
                "u": admin_id,
            },
        )
    ).one()
    await record(
        session,
        AuditEvent(
            action="admin.plivo_number_assigned",
            entity_type="plivo_number_assignment",
            entity_id=body.plivo_number.strip(),
            new_value={
                "tenant_id": str(body.tenant_id),
                "assigned_from": str(body.assigned_from),
                "assigned_to": str(body.assigned_to) if body.assigned_to else None,
                "monthly_rate_paise": body.monthly_rate_paise,
            },
            actor_user_id=admin_id,
            actor_role="super_admin",
        ),
        tenant_id=body.tenant_id,
    )
    return PlivoNumberAssignmentOut(**row._mapping)


async def list_plivo_assignments(
    session: AsyncSession, tenant_id: uuid.UUID | None = None
) -> list[PlivoNumberAssignmentOut]:
    rows = (
        await session.execute(_PLIVO_ASSIGNMENTS, {"t": str(tenant_id) if tenant_id else None})
    ).all()
    return [PlivoNumberAssignmentOut(**r._mapping) for r in rows]


_RESOLVE_NUMBER_TENANT = text(
    "SELECT tenant_id FROM plivo_number_assignments WHERE plivo_number = :num"
    " AND assigned_from <= :d AND (assigned_to IS NULL OR assigned_to >= :d)"
    " ORDER BY assigned_from DESC LIMIT 1"
)


async def ingest_plivo_call(
    session: AsyncSession,
    *,
    call_uuid: str,
    direction: Literal["inbound", "outbound"],
    from_number: str,
    to_number: str,
    duration_seconds: int,
    billed_seconds: int,
    call_status: str,
    provider_charge_inr: Decimal | None,
    raw: dict[str, Any],
) -> PlivoCallOut:
    settings = await load_settings(session)
    plivo_number = to_number if direction == "inbound" else from_number
    call_date = timeutil.to_ist(timeutil.utc_now()).date()
    resolved = (
        await session.execute(_RESOLVE_NUMBER_TENANT, {"num": plivo_number, "d": call_date})
    ).first()
    tenant_id = resolved.tenant_id if resolved else None
    cost_paise = calc.plivo_call_cost_paise(
        direction,
        billed_seconds,
        settings.plivo_inbound_inr_per_min,
        settings.plivo_outbound_inr_per_min,
    )
    period_start = call_date.replace(day=1)
    row = (
        await session.execute(
            _PLIVO_CALL_UPSERT,
            {
                "call_uuid": call_uuid,
                "t": tenant_id,
                "direction": direction,
                "from_number": from_number,
                "to_number": to_number,
                "duration": duration_seconds,
                "billed": billed_seconds,
                "status": call_status,
                "charge": provider_charge_inr,
                "cost": cost_paise,
                "period": period_start,
                "voice_call_id": None,
                "raw": json.dumps(raw, ensure_ascii=False),
            },
        )
    ).one()
    return PlivoCallOut(**row._mapping)


async def list_plivo_calls(
    session: AsyncSession, period_start: date, tenant_id: uuid.UUID | None = None
) -> PlivoCallsOut:
    rows = (
        await session.execute(
            _PLIVO_CALLS_LIST, {"p": period_start, "t": str(tenant_id) if tenant_id else None}
        )
    ).all()
    return PlivoCallsOut(calls=[PlivoCallOut(**r._mapping) for r in rows])


# ---------------------------------------------------------------- STT/TTS per-call usage
async def record_speech_usage(
    session: AsyncSession, body: SpeechUsageIn, admin_id: uuid.UUID
) -> SpeechUsageOut | None:
    rates = await load_speech_rates(session)
    rate_row = next((r for r in rates.rates if r.provider == body.provider), None)
    rate = rate_row.rate_inr if rate_row else Decimal(0)
    unit = rate_row.unit if rate_row else ""
    cost_paise = calc.speech_usage_cost_paise(body.quantity, rate)
    period_start = timeutil.to_ist(timeutil.utc_now()).date().replace(day=1)
    key = body.event_key.strip() or f"{body.voice_call_id}:{body.provider}:{uuid.uuid4()}"
    row = (
        await session.execute(
            _SPEECH_USAGE_INSERT,
            {
                "key": key,
                "t": body.tenant_id,
                "call": body.voice_call_id,
                "provider": body.provider,
                "qty": body.quantity,
                "unit": unit,
                "rate": rate,
                "cost": cost_paise,
                "period": period_start,
                "u": admin_id,
            },
        )
    ).first()
    if row is None:
        return None
    await record(
        session,
        AuditEvent(
            action="admin.speech_usage_recorded",
            entity_type="speech_usage",
            entity_id=key,
            new_value={
                "provider": body.provider,
                "quantity": str(body.quantity),
                "cost_paise": cost_paise,
            },
            actor_user_id=admin_id,
            actor_role="super_admin",
        ),
        tenant_id=body.tenant_id,
    )
    return SpeechUsageOut(**row._mapping)


async def list_speech_usage(
    session: AsyncSession, period_start: date, tenant_id: uuid.UUID | None = None
) -> SpeechUsageListOut:
    rows = (
        await session.execute(
            _SPEECH_USAGE_LIST, {"p": period_start, "t": str(tenant_id) if tenant_id else None}
        )
    ).all()
    return SpeechUsageListOut(records=[SpeechUsageOut(**r._mapping) for r in rows])


# ---------------------------------------------------------------- GCP app-level metering
async def record_gcp_usage_event(
    session: AsyncSession, body: GcpUsageEventIn, admin_id: uuid.UUID
) -> GcpUsageEventOut | None:
    categories = await load_cost_categories(session)
    cat = next((c for c in categories.categories if c.category == body.category), None)
    if cat is None:
        raise AppError("E-NF-001", detail="unknown cost category")
    cost_usd = body.quantity * cat.usd_rate
    period_start = timeutil.to_ist(timeutil.utc_now()).date().replace(day=1)
    key = body.event_key.strip() or f"{body.tenant_id}:{body.category}:{uuid.uuid4()}"
    row = (
        await session.execute(
            _GCP_EVENT_INSERT,
            {
                "key": key,
                "t": body.tenant_id,
                "category": body.category,
                "qty": body.quantity,
                "unit": cat.unit,
                "rate": cat.usd_rate,
                "cost": cost_usd,
                "period": period_start,
                "u": admin_id,
            },
        )
    ).first()
    if row is None:
        return None
    await record(
        session,
        AuditEvent(
            action="admin.gcp_usage_event_recorded",
            entity_type="gcp_usage_event",
            entity_id=key,
            new_value={
                "category": body.category,
                "quantity": str(body.quantity),
                "cost_usd": str(cost_usd),
            },
            actor_user_id=admin_id,
            actor_role="super_admin",
        ),
        tenant_id=body.tenant_id,
    )
    return GcpUsageEventOut(**row._mapping)


async def list_gcp_usage_events(
    session: AsyncSession, period_start: date, tenant_id: uuid.UUID | None = None
) -> GcpUsageEventsOut:
    rows = (
        await session.execute(
            _GCP_EVENTS_LIST, {"p": period_start, "t": str(tenant_id) if tenant_id else None}
        )
    ).all()
    return GcpUsageEventsOut(events=[GcpUsageEventOut(**r._mapping) for r in rows])


# ---------------------------------------------------------------- GCP Billing Export import
async def import_gcp_billing(
    session: AsyncSession, body: GcpImportIn, admin_id: uuid.UUID
) -> GcpImportOut:
    period_start = calc.parse_period(body.period)
    batch_id = uuid.uuid4()
    inserted = 0
    for row in body.rows:
        row_key = hashlib.sha256(
            (
                f"{period_start}|{row.billing_account}|{row.service}|{row.sku}|"
                f"{row.resource_name}|{row.usage_quantity}|{row.cost_usd}"
            ).encode()
        ).hexdigest()
        result = await session.execute(
            _GCP_IMPORT_INSERT,
            {
                "batch": batch_id,
                "period": period_start,
                "account": row.billing_account,
                "service": row.service,
                "sku": row.sku,
                "qty": row.usage_quantity,
                "unit": row.usage_unit,
                "cost": row.cost_usd,
                "credits": row.credits_usd,
                "currency": row.currency,
                "resource": row.resource_name,
                "row_key": row_key,
                "u": admin_id,
            },
        )
        if result.first() is not None:
            inserted += 1
    imported_total = Decimal(
        (await session.execute(_GCP_IMPORT_TOTAL_FOR_PERIOD, {"p": period_start})).scalar_one()
    )
    await record(
        session,
        AuditEvent(
            action="admin.gcp_billing_imported",
            entity_type="gcp_billing_import",
            entity_id=str(batch_id),
            new_value={"period": body.period, "rows": len(body.rows), "inserted": inserted},
            actor_user_id=admin_id,
            actor_role="super_admin",
        ),
    )
    return GcpImportOut(
        import_batch_id=batch_id,
        inserted=inserted,
        duplicate_skipped=len(body.rows) - inserted,
        imported_total_usd=imported_total,
    )


async def run_reconciliation(
    session: AsyncSession, period_start: date, admin_id: uuid.UUID
) -> GcpReconciliationOut:
    """Compare the imported Billing Export total against our app-level metered + rate-card
    total for the period. If imported > metered, the gap is kept as an explicit Unallocated
    line rather than being guessed and spread across clients (per the no-arbitrary-allocation
    rule). If metered >= imported (or nothing imported yet), nothing is unallocated."""
    imported_total = Decimal(
        (await session.execute(_GCP_IMPORT_TOTAL_FOR_PERIOD, {"p": period_start})).scalar_one()
    )
    metered_total = Decimal(
        (await session.execute(_GCP_EVENTS_TOTAL_FOR_PERIOD, {"p": period_start})).scalar_one()
    )
    if imported_total > 0 and metered_total < imported_total:
        allocated = metered_total
        unallocated = imported_total - metered_total
    else:
        allocated = metered_total
        unallocated = Decimal(0)
    await session.execute(_GCP_UNALLOC_CLEAR, {"p": period_start})
    if unallocated > 0:
        await session.execute(
            _GCP_UNALLOC_INSERT,
            {
                "p": period_start,
                "category": "unattributed",
                "amount": unallocated,
                "reason": (
                    "Imported Billing Export total is higher than app-level metered + "
                    "rate-card allocated total for this period; the difference is kept as "
                    "Unallocated instead of being guessed."
                ),
            },
        )
    row = (
        await session.execute(
            _GCP_RECON_INSERT,
            {
                "p": period_start,
                "imported": imported_total,
                "metered": metered_total,
                "allocated": allocated,
                "unallocated": unallocated,
                "u": admin_id,
            },
        )
    ).one()
    await record(
        session,
        AuditEvent(
            action="admin.gcp_reconciliation_run",
            entity_type="gcp_reconciliation",
            entity_id=calc.period_label(period_start),
            new_value={
                "imported_total_usd": str(imported_total),
                "metered_total_usd": str(metered_total),
                "unallocated_usd": str(unallocated),
            },
            actor_user_id=admin_id,
            actor_role="super_admin",
        ),
    )
    return GcpReconciliationOut(**row._mapping)


async def load_reconciliation(
    session: AsyncSession, period_start: date
) -> GcpReconciliationOut | None:
    row = (await session.execute(_GCP_RECON_LATEST, {"p": period_start})).first()
    return GcpReconciliationOut(**row._mapping) if row else None


async def list_unallocated(session: AsyncSession, period_start: date) -> GcpUnallocatedListOut:
    rows = (await session.execute(_GCP_UNALLOC_LIST, {"p": period_start})).all()
    return GcpUnallocatedListOut(items=[GcpUnallocatedOut(**r._mapping) for r in rows])


async def resolve_unallocated(
    session: AsyncSession, item_id: uuid.UUID, resolved: bool, admin_id: uuid.UUID
) -> None:
    row = (
        await session.execute(
            _GCP_UNALLOC_RESOLVE, {"id": item_id, "resolved": resolved, "u": admin_id}
        )
    ).first()
    if row is None:
        raise AppError("E-NF-001")
    await record(
        session,
        AuditEvent(
            action="admin.gcp_unallocated_resolved",
            entity_type="gcp_unallocated_cost",
            entity_id=str(item_id),
            new_value={"resolved": resolved},
            actor_user_id=admin_id,
            actor_role="super_admin",
        ),
    )
