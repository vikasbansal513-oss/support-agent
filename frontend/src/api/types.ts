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

export type IntegrationKind = "shopify" | "gupshup" | "gmail" | "shiprocket" | "delhivery";

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
