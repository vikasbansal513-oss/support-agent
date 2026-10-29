export type Role = "owner" | "agent";
export type Mode = "shadow" | "draft" | "auto";
export type Realm = "brand" | "admin";

export interface User {
  id: string;
  email: string;
  name: string;
  role: Role | "super_admin";
  tenant_id: string | null;
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

export interface Message {
  id: string;
  direction: "in" | "out";
  sender: "customer" | "agent" | "human" | "system";
  content: string;
  created_at: string;
  action_status: ActionStatus | null;
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

export type WriteTool = "address_update" | "order_cancel" | "exchange_request" | "refund_create";
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
  kind: string;
  severity: "info" | "warning" | "critical";
  message: string;
  status: "open" | "resolved";
  created_at: string;
  resolved_at: string | null;
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
  top_tenants: TenantRank[];
}

export interface PrivacyRequest {
  id: string;
  kind: "export" | "delete" | "correct";
  status: "open" | "done" | "rejected";
  due_at: string;
  created_at: string;
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

export interface VoiceSettings {
  configured: boolean;
  called_number: string | null;
  transfer_number: string | null;
  enabled: boolean;
  webhook_url: string | null;
  provider: string;
  telephony_mode: "ready_made" | "diy_plivo";
  providers: string[];
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

export interface BillingSettings {
  seller_name: string;
  seller_address: string;
  seller_state_code: string;
  seller_gstin: string;
  seller_email: string;
  seller_phone: string;
  payment_details: string;
  sac_code: string;
  invoice_prefix: string;
  gst_enabled: boolean;
  gst_rate_percent: string;
  cloudrun_vcpu: string;
  cloudrun_gib: string;
  cloudrun_usd_per_vcpu_second: string;
  cloudrun_usd_per_gib_second: string;
  updated_at: string | null;
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
}

export interface InvoiceSummary {
  id: string;
  tenant_id: string;
  status: "draft" | "final";
  invoice_no: string | null;
  total_paise: number;
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
  kind: "llm" | "cloudrun" | "telephony" | "platform_fee";
  description: string;
  quantity: string;
  unit: string;
  amount_paise: number;
  detail: Record<string, unknown>;
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
  doc_type: "tax_invoice" | "proforma";
  invoice_no: string | null;
  invoice_date: string | null;
  usd_inr: string;
  cloudrun_basis: "estimate" | "actual";
  llm_paise: number;
  cloudrun_paise: number;
  telephony_paise: number;
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
