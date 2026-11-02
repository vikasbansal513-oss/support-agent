"""Schemas for the enterprise usage-metering + billing-intelligence layer on top of the
existing Admin -> Billing section (overview, client-wise billing, usage ledger, reports,
usage limits/alerts). Kept in a separate file from billing/schemas.py so the existing
invoice/period/GCP/Plivo/speech schemas stay untouched."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Money = Annotated[Decimal, Field(max_digits=18, decimal_places=4)]
DatePreset = Literal["today", "this_week", "this_month", "previous_month", "custom"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RangeOut(BaseModel):
    preset: DatePreset
    start: date
    end: date  # inclusive, IST calendar date


# ---------------------------------------------------------------- A. Billing Overview
class CostBreakdownOut(BaseModel):
    llm_cost_inr: Money
    cloudrun_cost_inr: Money
    speech_cost_inr: Money
    plivo_cost_inr: Money
    other_gcp_cost_inr: Money
    total_operating_cost_inr: Money


class OverviewOut(BaseModel):
    range: RangeOut
    total_revenue_inr: Money
    total_client_usage_cost_inr: Money
    costs: CostBreakdownOut
    gross_profit_inr: Money
    profit_margin_percent: Decimal
    unallocated_infra_cost_inr: Money
    active_clients: int
    unpriced_llm_models: list[str]
    note: str = (
        "Revenue = finalized invoices with invoice_date in range (draft invoices are not"
        " revenue yet). Costs are metered from llm_calls, jobs, gcp_usage_events,"
        " plivo_call_records, speech_usage_records for the same range. Unallocated infra"
        " cost is from the latest GCP reconciliation run that falls in range, if any."
    )


# ---------------------------------------------------------------- B. Client-wise billing
class MessageUsageOut(BaseModel):
    incoming: int
    outgoing: int
    total: int


class AgentPerformanceOut(BaseModel):
    total_tasks: int
    completed: int
    failed: int
    pending: int
    success_rate_percent: Decimal
    avg_execution_seconds: Decimal
    total_execution_seconds: Decimal
    retry_count: int
    cost_per_successful_task_inr: Money


class LlmUsageLineOut(BaseModel):
    provider: str
    model: str
    api_calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    input_cost_inr: Money
    output_cost_inr: Money
    total_cost_inr: Money
    priced: bool


class GcpUsageLineOut(BaseModel):
    category: str
    quantity: Decimal
    unit: str
    cost_usd: Money
    cost_inr: Money


class OtherServiceCostOut(BaseModel):
    plivo_outbound_inr: Money
    plivo_inbound_inr: Money
    plivo_number_rental_inr: Money
    stt_cost_inr: Money
    tts_cost_inr: Money
    other_third_party_inr: Money


class ClientFinancialSummaryOut(BaseModel):
    total_llm_cost_inr: Money
    total_cloud_cost_inr: Money
    total_calling_cost_inr: Money
    total_other_cost_inr: Money
    total_operating_cost_inr: Money
    client_revenue_inr: Money
    gross_profit_inr: Money
    profit_margin_percent: Decimal


class ClientBillingSummaryOut(BaseModel):
    """One row per client, used in the Client-wise Billing list."""

    tenant_id: uuid.UUID
    tenant_name: str
    slug: str
    billing_status: str
    messages_total: int
    total_operating_cost_inr: Money
    client_revenue_inr: Money
    gross_profit_inr: Money
    profit_margin_percent: Decimal


class ClientInfoOut(BaseModel):
    tenant_id: uuid.UUID
    tenant_name: str
    slug: str
    active_brands: int
    plan: str
    billing_status: str
    billing_period: str


class ClientBillingOut(BaseModel):
    range: RangeOut
    client: ClientInfoOut
    messages: MessageUsageOut
    agent_performance: AgentPerformanceOut
    llm_usage: list[LlmUsageLineOut]
    gcp_usage: list[GcpUsageLineOut]
    other_costs: OtherServiceCostOut
    financial_summary: ClientFinancialSummaryOut


# ---------------------------------------------------------------- C. Usage Ledger
LedgerEventType = Literal[
    "message", "llm_call", "agent_execution", "gcp_usage", "plivo_call", "speech_usage"
]


class UsageLedgerRowOut(BaseModel):
    event_id: uuid.UUID
    tenant_id: uuid.UUID | None
    tenant_name: str
    event_type: LedgerEventType
    service_provider: str
    service_name: str
    resource_name: str
    quantity: Decimal
    unit: str
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: Decimal | None
    actual_cost_inr: Money | None
    estimated_cost_inr: Money | None
    status: str
    created_at: datetime


class UsageLedgerOut(BaseModel):
    range: RangeOut
    rows: list[UsageLedgerRowOut]
    next_cursor: str | None
    note: str = (
        "A cross-service read-only view over existing tables (messages, llm_calls, agent_runs,"
        " gcp_usage_events, plivo_call_records, speech_usage_records) -- not a duplicated"
        " ledger table, so there is nothing new to deduplicate: each source table already"
        " enforces its own idempotency (unique event_key / call_uuid / idempotency_key)."
    )


class UsageLedgerFilterIn(Strict):
    tenant_id: uuid.UUID | None = None
    event_type: LedgerEventType | None = None
    service_provider: str | None = Field(default=None, max_length=100)
    service_name: str | None = Field(default=None, max_length=100)
    start: date
    end: date
    cursor: str | None = None
    limit: Annotated[int, Field(ge=1, le=500)] = 100


# ---------------------------------------------------------------- Reports
ReportType = Literal[
    "client_usage",
    "llm",
    "gcp",
    "plivo",
    "agent_performance",
    "profitability",
    "actual_vs_estimated",
    "unallocated",
]


class ReportRowOut(BaseModel):
    columns: list[str]
    rows: list[list[str]]


# ---------------------------------------------------------------- Usage limits + alerts
UsageMetric = Literal["messages_month", "total_cost_inr_month", "llm_cost_inr_month"]


class UsageLimitIn(Strict):
    tenant_id: uuid.UUID
    metric: UsageMetric
    limit_value: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]
    active: bool = True


class UsageLimitOut(UsageLimitIn):
    id: uuid.UUID
    updated_at: datetime


class UsageLimitsOut(BaseModel):
    limits: list[UsageLimitOut]


class UsageLimitStatusOut(BaseModel):
    tenant_id: uuid.UUID
    tenant_name: str
    metric: UsageMetric
    limit_value: Decimal
    current_value: Decimal
    percent_used: Decimal
    breached_70: bool
    breached_90: bool
    breached_100: bool


class UsageLimitStatusesOut(BaseModel):
    statuses: list[UsageLimitStatusOut]


# ---------------------------------------------------------------- GCP Billing Export sync status
class BillingExportSyncStatusOut(BaseModel):
    status: Literal["not_connected", "connected"]
    project_ref: str
    last_sync_at: datetime | None
    last_sync_rows: int
    last_error: str
    import_batches: int
    note: str = (
        "No automated BigQuery pull is wired up yet -- rows are imported via"
        " POST /admin/billing/gcp/import (manual paste/CSV today, or a script you run)."
        " This endpoint only reports the status of imports already received, honestly:"
        " it never shows 'connected' unless billing_settings.billing_export_status says so."
    )


# ---------------------------------------------------------------- Client-facing own-usage view
class ClientUsageOut(BaseModel):
    range: RangeOut
    messages: MessageUsageOut
    agent_performance: AgentPerformanceOut
    llm_cost_inr: Money
    cloud_cost_inr: Money
    calling_cost_inr: Money
    total_usage_cost_inr: Money
    billing_history: list[ClientFinancialSummaryOut] = Field(default_factory=list)
