export type Role = "owner" | "agent";
export type Mode = "shadow" | "draft" | "auto";
export type Realm = "brand" | "admin";

export interface User {
  id: string;
  email: string;
  name: string;
  role: Role | "super_admin";
  tenant_id: string | null;
  tenant_name: string | null;
  mfa_ok: boolean;
}

export interface LoginOut {
  access_token: string;
  expires_in: number;
  csrf_token: string | null;
  user: User;
}

export interface MfaChallenge {
  mfa_required: true;
  mfa_token: string;
  expires_in: number;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface Customer {
  id: string;
  masked_phone: string | null;
  masked_email: string | null;
}

export interface Conversation {
  id: string;
  channel: "whatsapp" | "email" | "voice";
  status: "open" | "handoff" | "resolved" | "closed";
  customer: Customer;
  active_order_ref: string | null;
  language: "hi" | "en" | "hinglish" | null;
  human_active: boolean;
  last_message_at: string | null;
}

export type ActionStatus =
  | "claimed"
  | "shadow"
  | "draft"
  | "sent"
  | "failed"
  | "needs_review"
  | "rejected"
  | "suppressed";

export interface EmailClassificationSummary {
  category: string;
  action: "PROCESS" | "SKIP" | "SKIP_DUPLICATE" | "MANUAL_REVIEW" | "QUARANTINE";
  reply_allowed: boolean;
}

export interface Message {
  id: string;
  direction: "in" | "out";
  sender: "customer" | "agent" | "human" | "system";
  content: string;
  created_at: string;
  action_status: ActionStatus | null;
  email_classification?: EmailClassificationSummary | null;
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
}

export interface HandoffSummary {
  customer_masked_phone: string | null;
  order_ref: string | null;
  customer_asked: string;
  agent_did: string[];
  reason_code: string;
  why_stuck: string;
  language: string;
}

export interface Handoff {
  id: string;
  conversation_id: string;
  reason_code: string;
  priority: "normal" | "urgent";
  status: "open" | "claimed" | "resolved";
  would_handoff: boolean;
  summary: HandoffSummary;
  sla_due_at: string | null;
  sla_breached: boolean;
  created_at: string;
}

export interface Action {
  id: string;
  conversation_id: string;
  action_type: "reply_customer" | "handoff_notify" | "tool_call";
  status: ActionStatus;
  body_text: string | null;
  customer_message: string | null;
  verdict: "correct" | "wrong" | null;
  created_at: string;
}

export interface DashboardStats {
  day_ist: string;
  messages_today: number;
  handled_by_agent_today: number;
  handed_off_today: number;
  open_handoffs: number;
  shadow_correct: number;
  shadow_wrong: number;
  shadow_unreviewed: number;
  mode: Mode;
  agent_paused: boolean;
}

export interface Usage {
  period_start: string;
  conversation_count: number;
  limit: number;
  limit_reached: boolean;
}

export interface AgentStatus {
  mode: Mode;
  agent_paused: boolean;
  tenant_status: string;
  published_policy_version: number | null;
  integration_errors: string[];
}

export type IntegrationKind =
  | "shopify"
  | "gupshup"
  | "gmail"
  | "shiprocket"
  | "delhivery"
  | "bluedart"
  | "dtdc"
  | "ecom_express"
  | "xpressbees"
  | "shadowfax";

export interface Integration {
  kind: IntegrationKind;
  configured: boolean;
  status: "ok" | "auth_error" | "disabled" | "not_configured";
  config: Record<string, string>;
  secret_hint: string | null;
  last_error_code: string | null;
  last_checked_at: string | null;
  webhook_url: string | null;
}

export interface GmailConnect {
  url: string;
}

export interface ProbeResult {
  ok: boolean;
  detail: string | null;
  error_code: string | null;
}

export interface GmailCheckNow {
  queued: boolean;
  job_id: string | null;
  already_running: boolean;
}

export interface OfficeHours {
  start: string;
  end: string;
  days: number[];
}

export interface Disclosure {
  hi: string;
  en: string;
  hinglish: string;
}

export type WriteTool = "address_update" | "order_cancel" | "exchange_request" | "return_request" | "refund_create";
export type Level = "off" | "approval" | "auto";
export type Autonomy = Record<WriteTool, Level> & { order_status: "auto" };

export interface Limits {
  cancel_auto_limit_paise: number;
  exchange_auto_limit_paise: number;
  refund_auto_limit_paise: number;
  refund_customer_daily_paise: number;
  brand_daily_auto_value_paise: number;
  high_value_threshold_paise: number;
}

export interface PolicyContent {
  schema_version: 1 | 2;
  autonomy?: Autonomy;
  limits?: Limits;
  office_hours: OfficeHours;
  return_window_days: number;
  final_sale_tags: string[];
  refund_auto_limit_paise?: number | null;
  refund_customer_daily_limit_paise?: number | null;
  customer_daily_msg_cap: number;
  brand_daily_msg_cap: number;
  handoff_sla_minutes: number;
  ai_disclosure: Disclosure;
  extra_handoff_keywords: string[];
  enabled_tools: string[];
  reply_language?: "hi" | "en" | "hinglish";
}

export interface Policy {
  id: string;
  version: number;
  status: "draft" | "published" | "archived";
  content: PolicyContent;
  created_at: string;
  published_at: string | null;
  regression_run_id: string | null;
}

export interface Regression {
  run_id: string;
  passed: boolean;
  scenarios_total: number;
  scenarios_failed: string[];
  eval_subset_passed: boolean;
}

export interface NotificationSettings {
  notify_email_enabled: boolean;
  notify_email: string | null;
  notify_whatsapp_enabled: boolean;
  alert_phone: string | null;
  grievance_email: string | null;
}

export interface MfaStatus {
  enabled: boolean;
  required: boolean;
  recovery_remaining: number;
}

export interface Tenant {
  id: string;
  name: string;
  slug: string;
  status: string;
  agent_paused: boolean;
  mode: Mode;
  plan: string;
  included_conversations: number;
  usage_override_limit: number | null;
  created_at: string;
  approved_at: string | null;
}

export interface Job {
  id: string;
  tenant_id: string;
  kind: string;
  status: string;
  attempts: number;
  error_code: string | null;
  error_id: string | null;
  created_at: string;
}

export interface LlmUsageRow {
  tenant_id: string;
  day: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  est_cost_micro: number | null;
  p95_latency_ms: number | null;
}

export interface CallsByDay {
  day: string;
  calls: number;
}

export interface Overview {
  tenants_total: number;
  tenants_active: number;
  tenants_pending: number;
  tenants_paused: number;
  handoffs_open: number;
  jobs_failed: number;
  jobs_needs_review: number;
  today_calls: number;
  today_cost_micro: number;
  calls_by_day: CallsByDay[];
}

export interface Health {
  status: "ok" | "degraded";
  db: "ok" | "down";
  worker_last_seen_at: string | null;
  worker_seconds_since: number | null;
  queue_depth: number;
  jobs_failed_today: number;
  jobs_needs_review_today: number;
}

export interface TenantStats {
  tenant_id: string;
  messages_today: number;
  monthly_cost_micro: number;
  last_activity_at: string | null;
}

export interface Quota {
  tenant_id: string;
  monthly_call_limit: number | null;
  warn_percent: number;
  calls_this_month: number;
  usage_percent: number | null;
  updated_at: string | null;
}

export interface Alert {
  id: string;
  tenant_id: string | null;
  tenant_name?: string | null;
  kind: string;
  severity: "info" | "warning" | "high" | "critical";
  message: string;
  status: "open" | "acknowledged" | "resolved";
  service?: string;
  affected_agent?: string | null;
  error_id?: string | null;
  root_cause?: string | null;
  created_at: string;
  acknowledged_at?: string | null;
  resolution_notes?: string | null;
  resolved_at: string | null;
  resolution_minutes?: number | null;
}

export interface AuditLogRow {
  id: string;
  occurred_at: string;
  tenant_id: string | null;
  actor_user_id: string | null;
  actor_role: string | null;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  reason: string | null;
}

export interface TenantRank {
  tenant_id: string;
  name: string;
  handled: number;
  handed_off: number;
  handoff_rate_percent: number;
}

export interface ControlTower {
  health: Health;
  error_rate_percent: number;
  skipped_count: number;
  p95_latency_ms: number | null;
  failed_jobs_today: number;
  failed_jobs_avg_7d: number;
  needs_review_jobs_today: number;
  needs_review_jobs_avg_7d: number;
  top_tenants: TenantRank[];
}

// ------------------------------------------------------------------ Control Tower monitoring

export type AgentTaskStatus = "running" | "done" | "failed" | "skipped";
export type Correctness = "correct" | "wrong" | "partial" | "unverified";

export interface AgentTaskRow {
  run_id: string;
  tenant_id: string;
  tenant_name: string;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  task_category: string;
  status: AgentTaskStatus;
  correctness: Correctness;
  handed_off: boolean;
  llm_cost_micro: number | null;
  llm_model: string | null;
}

export interface AgentTaskList {
  items: AgentTaskRow[];
  total: number;
}

export interface AgentTaskLlmCall {
  purpose: string;
  provider: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number;
  outcome: string;
  created_at: string;
}

export interface AgentTaskAction {
  action_type: string;
  status: string;
  tool_name: string | null;
  body_text: string | null;
  error: string | null;
  created_at: string;
}

export interface AgentTaskDetail {
  run_id: string;
  tenant_id: string;
  tenant_name: string;
  status: AgentTaskStatus;
  started_at: string;
  finished_at: string | null;
  error_id: string | null;
  trace: Record<string, unknown>[];
  decision: Record<string, unknown> | null;
  actions: AgentTaskAction[];
  llm_calls: AgentTaskLlmCall[];
  verdict: string | null;
  report_reason: string | null;
}

export interface AccuracyBucket {
  total_evaluated: number;
  correct: number;
  incorrect: number;
  partially_correct: number;
  unverified: number;
  accuracy_percent: number;
  task_success_percent: number;
  human_correction_percent: number;
  handoff_percent: number;
}

export interface AccuracyTenant extends AccuracyBucket {
  tenant_id: string;
  tenant_name: string;
}

export interface Accuracy {
  overall: AccuracyBucket;
  by_tenant: AccuracyTenant[];
}

export interface PagePerfRow {
  tenant_id: string;
  tenant_name: string;
  route: string;
  samples: number;
  avg_load_ms: number | null;
  p50_load_ms: number | null;
  p95_load_ms: number | null;
  p99_load_ms: number | null;
  avg_lcp_ms: number | null;
  avg_ttfb_ms: number | null;
  failed_api_count: number;
  status: "good" | "slow";
}

export interface PagePerf {
  rows: PagePerfRow[];
  slowest_client: string | null;
  slowest_page: string | null;
  slow_threshold_ms: number;
}

export interface PagePerfBeacon {
  route: string;
  ttfb_ms?: number | null;
  dom_content_loaded_ms?: number | null;
  load_ms?: number | null;
  lcp_ms?: number | null;
  inp_ms?: number | null;
  cls?: number | null;
  api_duration_ms?: number | null;
  failed_api?: boolean;
}

export interface ClientTrafficRow {
  tenant_id: string;
  name: string;
  agent_runs_24h: number;
  integration_calls_1h: number;
  avg_integration_latency_ms: number | null;
  failed_integration_calls_1h: number;
}

export interface Traffic {
  total_clients: number;
  active_clients: number;
  active_users_5m: number;
  running_jobs: number;
  queue_depth: number;
  needs_review_jobs_recent: number;
  failed_jobs_recent: number;
  db_connections: number;
  integration_calls_per_minute: number;
  avg_integration_latency_ms: number | null;
  p95_integration_latency_ms: number | null;
  integration_error_rate_percent: number;
  process_rss_mb: number;
  by_client: ClientTrafficRow[];
  note: string;
}

export interface HistoricalPoint {
  bucket: string;
  value: number;
}

export interface HistoricalSeries {
  name: string;
  points: HistoricalPoint[];
}

export type MonitorWindow = "24h" | "7d" | "30d" | "90d";

export interface Historical {
  range: MonitorWindow;
  series: HistoricalSeries[];
}

export interface Threshold {
  key: string;
  value: number;
}

export interface AgentPerformanceRow {
  agent_key: string;
  graph_version: string;
  task_category: string;
  total_tasks: number;
  successful_tasks: number;
  failed_tasks: number;
  skipped_tasks: number;
  sent_replies: number;
  correct: number;
  incorrect: number;
  partially_correct: number;
  unverified: number;
  total_evaluated: number;
  accuracy_percent: number | null;
  task_success_percent: number;
  human_corrections: number;
  human_correction_percent: number;
  handoff_count: number;
  handoff_percent: number;
  avg_execution_ms: number | null;
  top_model: string | null;
  insufficient_data: boolean;
  rank: number;
}

export interface AgentPerformance {
  rows: AgentPerformanceRow[];
  min_evaluated_for_ranking: number;
}

export interface ProviderErrorCategory {
  error_class: string;
  count: number;
}

export interface ClientProviderRow {
  provider: string;
  total_calls: number;
  successful_calls: number;
  failed_calls: number;
  success_percent: number;
  avg_latency_ms: number | null;
  p95_latency_ms: number | null;
  health: "healthy" | "degraded" | "failed" | "unknown";
  last_success_at: string | null;
  last_failure_at: string | null;
  top_error_categories: ProviderErrorCategory[];
}

export interface ClientMonitoring {
  tenant_id: string;
  tenant_name: string;
  tenant_status: string;
  window: MonitorWindow;
  runs_total: number;
  runs_done: number;
  runs_failed: number;
  runs_skipped: number;
  accuracy: AccuracyBucket;
  llm_calls_total: number;
  llm_calls_failed: number;
  llm_avg_latency_ms: number | null;
  llm_p95_latency_ms: number | null;
  llm_cost_micro: number;
  integration_calls_total: number;
  integration_calls_failed: number;
  integration_avg_latency_ms: number | null;
  integration_p95_latency_ms: number | null;
  by_provider: ClientProviderRow[];
  worst_agent_key: string | null;
  worst_agent_accuracy_percent: number | null;
  worst_provider: string | null;
}

export interface PrivacyRequest {
  id: string;
  kind: "export" | "delete" | "correct";
  status: "open" | "done" | "rejected";
  due_at: string;
  created_at: string;
  correction_field: "phone" | "email" | null;
  correction_value: string | null;
}

export interface PublicNotice {
  version: string;
  draft: boolean;
  grievance_email: string | null;
  html: string;
}

export type LlmProvider = "anthropic" | "openai" | "groq" | "openrouter" | "openai_compatible";

export interface LlmSettings {
  provider: LlmProvider;
  model: string;
  base_url: string;
  has_key: boolean;
  source: "dashboard" | "deploy";
  updated_at: string | null;
}

export interface LlmTestResult {
  ok: boolean;
  latency_ms: number;
  error: string | null;
}

export interface GlobalState {
  status: "paused" | "running";
}

export type ApprovalStatus =
  | "awaiting_customer"
  | "pending_approval"
  | "approved"
  | "claimed"
  | "executed"
  | "confirmed"
  | "needs_review"
  | "rejected"
  | "failed"
  | "expired";

export interface Approval {
  id: string;
  conversation_id: string;
  tool: WriteTool;
  order_ref: string | null;
  amount_paise: number;
  status: ApprovalStatus;
  requires_approval: boolean;
  risk_tier: string | null;
  reason: string | null;
  error: string | null;
  external_ref: string | null;
  created_at: string;
  expires_at: string | null;
  approved_at: string | null;
  sla_due_at: string | null;
  overdue: boolean;
}

export interface ApprovalDetail extends Approval {
  timeline: { at: string; action: string; actor_role: string | null; reason: string | null }[];
}

export interface ApprovalOutcome {
  action_id: string;
  status: string;
  detail: string | null;
}

export interface ToolKill {
  tool: WriteTool;
  killed: boolean;
  reason: string | null;
  at: string | null;
}

export type Carrier = "jio" | "airtel" | "vi" | "bsnl" | "other";

export interface VoiceSettings {
  configured: boolean;
  called_number: string | null;
  transfer_number: string | null;
  enabled: boolean;
  webhook_url: string | null;
  provider: string;
  telephony_mode: "ready_made" | "diy_plivo";
  providers: string[];
  client_public_number: string | null;
  carrier: Carrier | null;
  forwarding_confirmed: boolean;
  forwarding_code: string | null;
  forwarding_off_code: string | null;
}

export interface VoiceCall {
  id: string;
  status: "in_progress" | "completed" | "transferred" | "callback" | "failed";
  caller_masked: string | null;
  turn_count: number;
  end_reason: string | null;
  started_at: string;
  ended_at: string | null;
  conversation_id: string;
  order_ref: string | null;
  avg_latency_ms: number;
  max_latency_ms: number;
  slow_turns: number;
  feedback: "good" | "bad" | null;
  feedback_note: string | null;
  summary_status: "sent" | "skipped" | "failed" | null;
}

export interface VoiceStats {
  days: number;
  calls: number;
  handled_by_agent: number;
  transferred: number;
  callback: number;
  failed: number;
  avg_turns: number;
  avg_latency_ms: number;
  slow_turns: number;
  feedback_good: number;
  feedback_bad: number;
  agent_rate_percent: number;
}

export interface VoiceTurn {
  at: string;
  speaker: "caller" | "agent";
  text: string;
}

export interface VoiceCallDetail extends VoiceCall {
  turns: VoiceTurn[];
}

export type BillingExportStatus = "not_connected" | "connected";
export type ReconciliationCycle = "monthly" | "daily_preview_monthly_final";
export type UnallocatedHandling = "keep_unallocated" | "manual_review";
export type SharedAllocationPolicy = "per_sku" | "usage_weighted";

export interface BillingSettings {
  seller_name: string;
  seller_address: string;
  seller_state_code: string;
  seller_gstin: string;
  seller_email: string;
  seller_phone: string;
  seller_pincode: string;
  payment_details: string;
  sac_code: string;
  invoice_prefix: string;
  gst_enabled: boolean;
  gst_rate_percent: string;
  cloudrun_vcpu: string;
  cloudrun_gib: string;
  cloudrun_usd_per_vcpu_second: string;
  cloudrun_usd_per_gib_second: string;
  billing_export_status: BillingExportStatus;
  billing_export_ref: string;
  reconciliation_cycle: ReconciliationCycle;
  unallocated_handling: UnallocatedHandling;
  shared_allocation_policy: SharedAllocationPolicy;
  plivo_outbound_inr_per_min: string;
  plivo_inbound_inr_per_min: string;
  plivo_number_rental_inr_per_month: string;
  updated_at: string | null;
}

export interface CostCategory {
  category: string;
  unit: string;
  usd_rate: string;
  attribution: string;
  active: boolean;
  sort_order: number;
  updated_at: string;
}

export interface CostCategoriesOut {
  categories: CostCategory[];
}

export interface BillingLlmRate {
  provider: string;
  model: string;
  input_usd_per_million: string;
  output_usd_per_million: string;
  updated_at: string;
}

export interface BillingRates {
  rates: BillingLlmRate[];
  unpriced: { provider: string; model: string }[];
}

export type SpeechProvider = "sarvam_stt" | "google_stt" | "sarvam_tts" | "google_tts";

export interface SpeechRate {
  provider: SpeechProvider;
  unit: string;
  rate_inr: string;
  active: boolean;
  updated_at: string;
}

export interface SpeechRatesOut {
  rates: SpeechRate[];
}

export type BillingStatus = "enabled" | "paused";

export interface BillingProfile {
  tenant_id: string;
  tenant_name: string;
  slug: string;
  legal_name: string;
  address: string;
  state_code: string;
  gstin: string;
  email: string;
  default_platform_fee_paise: number;
  cloud_enabled: boolean;
  llm_enabled: boolean;
  plivo_enabled: boolean;
  payment_due_days: number;
  auto_generate_draft: boolean;
  billing_status: BillingStatus;
}

export type PaymentStatus = "draft" | "paid" | "partially_paid" | "unpaid" | "overdue" | "cancelled";
export type TelephonyBasis = "actual" | "cdr_computed" | "estimate" | "none";

export interface InvoiceSummary {
  id: string;
  tenant_id: string;
  status: "draft" | "final";
  payment_status: PaymentStatus;
  invoice_no: string | null;
  due_date: string | null;
  total_paise: number;
  paid_amount_paise: number;
}

export interface BillingPeriodRow {
  tenant_id: string;
  name: string;
  slug: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  llm_paise: number | null;
  compute_seconds: number;
  jobs: number;
  cloudrun_paise: number | null;
  voice_calls: number;
  voice_call_seconds: number;
  telephony_paise: number | null;
  telephony_basis: TelephonyBasis;
  speech_paise: number;
  number_rental_paise: number;
  default_platform_fee_paise: number;
  unpriced_models: string[];
  invoice: InvoiceSummary | null;
}

export interface BillingPeriod {
  period: string;
  period_ended: boolean;
  usd_inr: string | null;
  cloudrun_actual_paise: number | null;
  cloudrun_unallocated_paise: number;
  telephony_actual_paise: number | null;
  telephony_unallocated_paise: number;
  rows: BillingPeriodRow[];
}

export interface BillingFlag {
  code: string;
  detail: string | null;
}

export interface InvoiceLine {
  position: number;
  kind: "llm" | "cloudrun" | "telephony" | "speech" | "platform_fee";
  description: string;
  quantity: string;
  unit: string;
  amount_paise: number;
  detail: Record<string, unknown>;
}

export interface InvoicePayment {
  id: string;
  amount_paise: number;
  paid_on: string;
  method: string;
  note: string;
  created_at: string;
}

export interface InvoiceSeller {
  name: string;
  address: string;
  state_code: string;
  gstin: string;
  email: string;
  phone: string;
  payment_details: string;
  sac_code: string;
}

export interface InvoiceBuyer {
  name: string;
  address: string;
  state_code: string;
  gstin: string;
  email: string;
}

export interface Invoice {
  id: string;
  tenant_id: string;
  tenant_name: string;
  period: string;
  period_start: string;
  status: "draft" | "final";
  payment_status: PaymentStatus;
  doc_type: "tax_invoice" | "proforma";
  invoice_no: string | null;
  invoice_date: string | null;
  due_date: string | null;
  paid_at: string | null;
  paid_amount_paise: number;
  balance_due_paise: number;
  cancelled_at: string | null;
  cancelled_reason: string;
  payments: InvoicePayment[];
  usd_inr: string;
  cloudrun_basis: "estimate" | "actual";
  llm_paise: number;
  cloudrun_paise: number;
  telephony_paise: number;
  speech_paise: number;
  platform_fee_paise: number;
  subtotal_paise: number;
  gst_rate_percent: string;
  gst_kind: "none" | "intra" | "inter";
  cgst_paise: number;
  sgst_paise: number;
  igst_paise: number;
  total_paise: number;
  seller: InvoiceSeller;
  buyer: InvoiceBuyer;
  lines: InvoiceLine[];
  blockers: BillingFlag[];
  warnings: BillingFlag[];
  generated_at: string;
  finalized_at: string | null;
}

export interface GenerateResult {
  generated: number;
  skipped_final: number;
  invoices: InvoiceSummary[];
}

export interface PlivoNumberAssignment {
  id: string;
  plivo_number: string;
  tenant_id: string;
  assigned_from: string;
  assigned_to: string | null;
  monthly_rate_paise: number;
  created_at: string;
}

export interface PlivoCall {
  id: string;
  call_uuid: string;
  tenant_id: string | null;
  direction: "inbound" | "outbound";
  from_number: string;
  to_number: string;
  duration_seconds: number;
  billed_seconds: number;
  call_status: string;
  computed_cost_paise: number;
  billing_period: string;
  created_at: string;
}

export interface PlivoCallsOut {
  calls: PlivoCall[];
}

export interface SpeechUsageRecord {
  id: string;
  tenant_id: string;
  voice_call_id: string | null;
  provider: SpeechProvider;
  quantity: string;
  unit: string;
  cost_paise: number;
  billing_period: string;
  created_at: string;
}

export interface SpeechUsageListOut {
  records: SpeechUsageRecord[];
}

export interface GcpUsageEvent {
  id: string;
  tenant_id: string | null;
  category: string;
  quantity: string;
  unit: string;
  cost_usd: string;
  billing_period: string;
  created_at: string;
}

export interface GcpUsageEventsOut {
  events: GcpUsageEvent[];
}

export interface GcpImportRow {
  billing_account?: string;
  service?: string;
  sku?: string;
  usage_quantity?: string;
  usage_unit?: string;
  cost_usd?: string;
  credits_usd?: string;
  currency?: string;
  resource_name?: string;
}

export interface GcpImportResult {
  import_batch_id: string;
  inserted: number;
  duplicate_skipped: number;
  imported_total_usd: string;
}

export interface GcpReconciliation {
  billing_period: string;
  imported_total_usd: string;
  metered_total_usd: string;
  allocated_usd: string;
  unallocated_usd: string;
  created_at: string;
}

export interface GcpUnallocated {
  id: string;
  billing_period: string;
  category: string;
  amount_usd: string;
  reason: string;
  resolved: boolean;
}

export interface GcpUnallocatedListOut {
  items: GcpUnallocated[];
}

// ---------------------------------------------------------------- Usage intelligence
export type DatePreset = "today" | "this_week" | "this_month" | "previous_month" | "custom";

export interface UsageRange {
  preset: DatePreset;
  start: string;
  end: string;
}

export interface CostBreakdown {
  llm_cost_inr: string;
  cloudrun_cost_inr: string;
  speech_cost_inr: string;
  plivo_cost_inr: string;
  other_gcp_cost_inr: string;
  total_operating_cost_inr: string;
}

export interface BillingOverview {
  range: UsageRange;
  total_revenue_inr: string;
  total_client_usage_cost_inr: string;
  costs: CostBreakdown;
  gross_profit_inr: string;
  profit_margin_percent: string;
  unallocated_infra_cost_inr: string;
  active_clients: number;
  unpriced_llm_models: string[];
  note: string;
}

export interface MessageUsage {
  incoming: number;
  outgoing: number;
  total: number;
}

export interface AgentPerformance {
  total_tasks: number;
  completed: number;
  failed: number;
  pending: number;
  success_rate_percent: string;
  avg_execution_seconds: string;
  total_execution_seconds: string;
  retry_count: number;
  cost_per_successful_task_inr: string;
}

export interface ClientBillingSummary {
  tenant_id: string;
  tenant_name: string;
  slug: string;
  billing_status: string;
  messages_total: number;
  total_operating_cost_inr: string;
  client_revenue_inr: string;
  gross_profit_inr: string;
  profit_margin_percent: string;
}

export interface LlmUsageLine {
  provider: string;
  model: string;
  api_calls: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  input_cost_inr: string;
  output_cost_inr: string;
  total_cost_inr: string;
  priced: boolean;
}

export interface GcpUsageLine {
  category: string;
  quantity: string;
  unit: string;
  cost_usd: string;
  cost_inr: string;
}

export interface OtherServiceCost {
  plivo_outbound_inr: string;
  plivo_inbound_inr: string;
  plivo_number_rental_inr: string;
  stt_cost_inr: string;
  tts_cost_inr: string;
  other_third_party_inr: string;
}

export interface ClientFinancialSummary {
  total_llm_cost_inr: string;
  total_cloud_cost_inr: string;
  total_calling_cost_inr: string;
  total_other_cost_inr: string;
  total_operating_cost_inr: string;
  client_revenue_inr: string;
  gross_profit_inr: string;
  profit_margin_percent: string;
}

export interface ClientInfo {
  tenant_id: string;
  tenant_name: string;
  slug: string;
  active_brands: number;
  plan: string;
  billing_status: string;
  billing_period: string;
}

export interface ClientBillingDetail {
  range: UsageRange;
  client: ClientInfo;
  messages: MessageUsage;
  agent_performance: AgentPerformance;
  llm_usage: LlmUsageLine[];
  gcp_usage: GcpUsageLine[];
  other_costs: OtherServiceCost;
  financial_summary: ClientFinancialSummary;
}

export type LedgerEventType =
  | "message"
  | "llm_call"
  | "agent_execution"
  | "gcp_usage"
  | "plivo_call"
  | "speech_usage";

export interface UsageLedgerRow {
  event_id: string;
  tenant_id: string | null;
  tenant_name: string;
  event_type: LedgerEventType;
  service_provider: string;
  service_name: string;
  resource_name: string;
  quantity: string;
  unit: string;
  started_at: string;
  ended_at: string | null;
  duration_seconds: string | null;
  actual_cost_inr: string | null;
  estimated_cost_inr: string | null;
  status: string;
  created_at: string;
}

export interface UsageLedgerOut {
  range: UsageRange;
  rows: UsageLedgerRow[];
  next_cursor: string | null;
  note: string;
}

export type ReportType =
  | "client_usage"
  | "llm"
  | "gcp"
  | "plivo"
  | "agent_performance"
  | "profitability"
  | "actual_vs_estimated"
  | "unallocated";

export interface ReportOut {
  columns: string[];
  rows: string[][];
}

export type UsageMetric = "messages_month" | "total_cost_inr_month" | "llm_cost_inr_month";

export interface UsageLimit {
  id: string;
  tenant_id: string;
  metric: UsageMetric;
  limit_value: string;
  active: boolean;
  updated_at: string;
}

export interface UsageLimitsOut {
  limits: UsageLimit[];
}

export interface UsageLimitStatus {
  tenant_id: string;
  tenant_name: string;
  metric: UsageMetric;
  limit_value: string;
  current_value: string;
  percent_used: string;
  breached_70: boolean;
  breached_90: boolean;
  breached_100: boolean;
}

export interface UsageLimitStatusesOut {
  statuses: UsageLimitStatus[];
}

export interface BillingExportSyncStatus {
  status: BillingExportStatus;
  project_ref: string;
  last_sync_at: string | null;
  last_sync_rows: number;
  last_error: string;
  import_batches: number;
  note: string;
}

export interface ClientUsageOut {
  range: UsageRange;
  messages: MessageUsage;
  agent_performance: AgentPerformance;
  llm_cost_inr: string;
  cloud_cost_inr: string;
  calling_cost_inr: string;
  total_usage_cost_inr: string;
  billing_history: ClientFinancialSummary[];
}
