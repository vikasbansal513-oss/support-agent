import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import { Guard } from "../App";
import { clearSession } from "../api/client";
import { Login } from "../pages/auth";
import { ConversationView } from "../pages/Conversations";
import { Dashboard } from "../pages/Dashboard";
import { Handoffs } from "../pages/Handoffs";
import { AdminOps } from "../pages/Admin";
import { NoticePage, PrivacyRequests } from "../pages/Privacy";
import { Review } from "../pages/Review";
import { Approvals } from "../pages/Approvals";
import { AutonomySettings } from "../pages/Autonomy";
import { VoiceSettings } from "../pages/Voice";
import { useEffect } from "react";
import { useAuth } from "../auth/AuthContext";
import { LOGIN_OK, mockFetch, renderApp } from "./helpers";
import { t } from "../i18n";

afterEach(() => {
  clearSession();
  vi.unstubAllGlobals();
});

const STATS = {
  day_ist: "2026-01-01", messages_today: 0, handled_by_agent_today: 0, handed_off_today: 0, open_handoffs: 0,
  shadow_correct: 0, shadow_wrong: 0, shadow_unreviewed: 0, mode: "shadow", agent_paused: false,
};

async function loginFirst() {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText(t.email), "o@brand.example");
  await user.type(screen.getByLabelText(t.password), "correct horse battery");
  await user.click(screen.getByRole("button", { name: t.login }));
  return user;
}

describe("login", () => {
  it("logs in and redirects to the dashboard", async () => {
    mockFetch((call) => {
      if (call.url.endsWith("/auth/login")) return { body: LOGIN_OK };
      if (call.url.endsWith("/dashboard/stats")) return { body: STATS };
      if (call.url.endsWith("/usage")) return { body: { period_start: "2026-01-01", conversation_count: 0, limit: 500, limit_reached: false } };
      if (call.url.endsWith("/agent/status")) return { body: { mode: "shadow", agent_paused: false, tenant_status: "active", published_policy_version: 1, integration_errors: [] } };
      return undefined;
    });
    renderApp(
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<Guard access="user"><Dashboard /></Guard>} />
      </Routes>,
      "/login",
    );
    await loginFirst();
    expect(await screen.findByText(t.emptyDashboard)).toBeInTheDocument();
  });

  it("asks for the MFA code when required", async () => {
    const calls = mockFetch((call) => {
      if (call.url.endsWith("/auth/login")) return { body: { mfa_required: true, mfa_token: "m".repeat(30), expires_in: 300 } };
      if (call.url.endsWith("/auth/mfa/verify")) return { body: LOGIN_OK };
      return { body: STATS };
    });
    renderApp(<Login />, "/login");
    const user = await loginFirst();
    await user.type(await screen.findByLabelText(t.mfaCode), "123456");
    await user.click(screen.getByRole("button", { name: t.login }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/auth/mfa/verify"))).toBe(true));
    expect(calls.find((c) => c.url.endsWith("/auth/mfa/verify"))?.body).toEqual({ mfa_token: "m".repeat(30), code: "123456" });
  });

  it("shows the error message with error id on wrong password", async () => {
    mockFetch(() => ({ status: 401, body: { code: "E-AUTH-001", message: "Email ya password galat hai.", error_id: "ERR-AAAAAAAAAAAA", severity: "warning" } }));
    renderApp(<Login />, "/login");
    await loginFirst();
    expect(await screen.findByText("Email ya password galat hai.")).toBeInTheDocument();
    expect(screen.getByText(/ERR-AAAAAAAAAAAA/)).toBeInTheDocument();
  });
});

describe("guard", () => {
  it("redirects anonymous users to login", async () => {
    mockFetch(() => undefined);
    renderApp(
      <Routes>
        <Route path="/login" element={<p>login page</p>} />
        <Route path="/" element={<Guard access="user"><p>secret</p></Guard>} />
      </Routes>,
      "/",
    );
    // AuthProvider tries to restore a session from the refresh cookie on
    // mount (see AuthContext.tsx) before Guard decides anything, so the
    // redirect lands after that resolves, not on the first render.
    await waitFor(() => expect(screen.getByText("login page")).toBeInTheDocument());
    expect(screen.queryByText("secret")).toBeNull();
  });
});

describe("screens", () => {
  it("shows unsent agent drafts as not sent in the thread", async () => {
    mockFetch((call) =>
      call.url.includes("/conversations/c1")
        ? {
            body: {
              id: "c1", channel: "whatsapp", status: "open", customer: { id: "x", masked_phone: "******3210", masked_email: null },
              active_order_ref: null, language: "hinglish", human_active: true, last_message_at: null,
              messages: [
                { id: "m1", direction: "in", sender: "customer", content: "order kahan hai", created_at: "2026-01-01T00:00:00Z", action_status: null },
                { id: "m2", direction: "out", sender: "agent", content: "ship ho gaya", created_at: "2026-01-01T00:01:00Z", action_status: "shadow" },
              ],
            },
          }
        : undefined,
    );
    renderApp(
      <Routes>
        <Route path="/conversations/:id" element={<ConversationView />} />
      </Routes>,
      "/conversations/c1",
    );
    expect(await screen.findByText("ship ho gaya")).toBeInTheDocument();
    expect(screen.getByText(new RegExp(t.notSent))).toBeInTheDocument();
    expect(screen.getByText(t.humanActive)).toBeInTheDocument();
    expect(screen.getByText("******3210")).toBeInTheDocument();
  });

  it("handoffs list shows empty state", async () => {
    mockFetch(() => ({ body: { items: [], next_cursor: null } }));
    renderApp(<Handoffs />);
    expect(await screen.findByText(t.emptyHandoffs)).toBeInTheDocument();
  });

  it("handoff card shows urgent chip and claims", async () => {
    const item = {
      id: "h1", conversation_id: "c1", reason_code: "LEGAL", priority: "urgent", status: "open", would_handoff: false,
      summary: { customer_masked_phone: "******3210", order_ref: "#1001", customer_asked: "legal notice", agent_did: [], reason_code: "LEGAL", why_stuck: "Legal", language: "en" },
      sla_due_at: new Date(Date.now() + 20 * 60000).toISOString(), sla_breached: false, created_at: "2026-01-01T00:00:00Z",
    };
    const calls = mockFetch((call) => {
      if (call.method === "POST") return { body: { ...item, status: "claimed" } };
      return { body: { items: [item], next_cursor: null } };
    });
    renderApp(<Handoffs />);
    expect(await screen.findByText(t.urgent)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: t.claim }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/handoffs/h1/claim"))).toBe(true));
  });

  it("review card posts a verdict with note", async () => {
    const action = { id: "a1", conversation_id: "c1", action_type: "reply_customer", status: "shadow", body_text: "Aapka order ship ho gaya", customer_message: "kahan hai", verdict: null, created_at: "2026-01-01T00:00:00Z" };
    const calls = mockFetch((call) => {
      if (call.url.endsWith("/agent/status")) return { body: { mode: "shadow", agent_paused: false, tenant_status: "active", published_policy_version: 1, integration_errors: [] } };
      if (call.method === "POST") return { body: { ...action, verdict: "wrong" } };
      return { body: { items: [action], next_cursor: null } };
    });
    renderApp(<Review />);
    const user = userEvent.setup();
    expect(await screen.findByText(/Aapka order ship ho gaya/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Review" }));
    expect(screen.queryByRole("button", { name: t.approve })).toBeNull();
    await user.type(screen.getByLabelText(t.note), "galat amount");
    await user.click(screen.getByRole("button", { name: t.verdictWrong }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/actions/a1/verdict"))).toBe(true));
    expect(calls.find((c) => c.url.endsWith("/actions/a1/verdict"))?.body).toEqual({ verdict: "wrong", note: "galat amount" });
  });

  it("review shows approve only in draft mode", async () => {
    const action = { id: "a2", conversation_id: "c1", action_type: "reply_customer", status: "draft", body_text: "draft jawab", customer_message: "hi", verdict: null, created_at: "2026-01-01T00:00:00Z" };
    mockFetch((call) => {
      if (call.url.endsWith("/agent/status")) return { body: { mode: "draft", agent_paused: false, tenant_status: "active", published_policy_version: 1, integration_errors: [] } };
      return { body: { items: [action], next_cursor: null } };
    });
    renderApp(<Review />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Review" }));
    expect(await screen.findByRole("button", { name: t.approve })).toBeInTheDocument();
  });
});

describe("privacy and notices", () => {
  it("creates a request and lists it", async () => {
    const rows = [{ id: "1", kind: "export", status: "open", due_at: "2026-02-01T00:00:00Z", created_at: "2026-01-02T00:00:00Z" }];
    const calls = mockFetch((call) => {
      if (call.method === "POST" && call.url.endsWith("/privacy/requests")) return { status: 201, body: rows[0] };
      if (call.url.endsWith("/privacy/requests")) return { body: rows };
      return undefined;
    });
    renderApp(<PrivacyRequests />, "/privacy");
    expect(await screen.findByText("open")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(t.privacySubject), "+919876543210");
    await user.click(screen.getByRole("button", { name: t.privacyCreate }));
    await waitFor(() =>
      expect(
        calls.some(
          (c) => c.method === "POST" && c.url.endsWith("/privacy/requests") && (c.body as { kind: string } | undefined)?.kind === "export",
        ),
      ).toBe(true),
    );
  });

  it("shows the draft banner on legal pages", async () => {
    mockFetch(() => ({ body: { version: "2026-01", draft: true, grievance_email: "g@brand.example", html: "<h1>Privacy notice</h1>" } }));
    renderApp(<NoticePage kind="privacy" />, "/privacy-notice");
    expect(await screen.findByText(t.noticeDraft)).toBeInTheDocument();
    expect(screen.getByText(/g@brand.example/)).toBeInTheDocument();
  });

  it("asks for a reason before the global pause", async () => {
    const calls = mockFetch((call) => {
      if (call.url.endsWith("/admin/global-pause") && call.method === "GET") return { body: { status: "running" } };
      if (call.url.endsWith("/admin/global-pause")) return { body: { status: "paused" } };
      if (call.url.endsWith("/admin/llm-usage")) return { body: [] };
      if (call.url.endsWith("/admin/llm-settings")) return { body: LLM_DEFAULT };
      return { body: { items: [], next_cursor: null } };
    });
    renderApp(<AdminOps />, "/admin/ops");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: t.globalPause }));
    await user.type(screen.getByLabelText(t.reasonLabel), "incident test");
    await user.click(screen.getByRole("button", { name: t.confirm }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/admin/global-pause"))).toBe(true));
  });
});

const DUMMY_KEY = ["gsk", "test", "key", "123456"].join("_");
const LLM_DEFAULT = { provider: "anthropic", model: "claude-haiku-5-5", base_url: "", has_key: true, source: "deploy", updated_at: null };

describe("admin LLM settings", () => {
  it("tests and saves a different provider with a reason", async () => {
    let saved: object = LLM_DEFAULT;
    const calls = mockFetch((call) => {
      if (call.url.endsWith("/admin/llm-settings/test")) return { body: { ok: true, latency_ms: 120, error: null } };
      if (call.url.endsWith("/admin/llm-settings") && call.method === "PUT") {
        saved = { ...LLM_DEFAULT, provider: "groq", model: "llama-3.3-70b-versatile", source: "dashboard", updated_at: "2026-01-01T00:00:00Z" };
        return { body: saved };
      }
      if (call.url.endsWith("/admin/llm-settings")) return { body: saved };
      if (call.url.endsWith("/admin/global-pause")) return { body: { status: "running" } };
      if (call.url.endsWith("/admin/llm-usage")) return { body: [] };
      return { body: { items: [], next_cursor: null } };
    });
    renderApp(<AdminOps />, "/admin/ops");
    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText(t.llmProvider), "groq");
    const model = screen.getByLabelText(t.llmModel);
    await user.clear(model);
    await user.type(model, "llama-3.3-70b-versatile");
    await user.type(screen.getByLabelText(t.llmApiKey), DUMMY_KEY);
    await user.click(screen.getByRole("button", { name: t.llmTest }));
    expect(await screen.findByText(/120 ms/)).toBeInTheDocument();
    expect(calls.find((c) => c.url.endsWith("/admin/llm-settings/test"))?.body).toEqual({
      provider: "groq",
      model: "llama-3.3-70b-versatile",
      base_url: "",
      api_key: DUMMY_KEY,
    });
    await user.click(screen.getByRole("button", { name: t.llmSave }));
    await user.type(screen.getByLabelText(t.reasonLabel), "groq par shift");
    await user.click(screen.getByRole("button", { name: t.confirm }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url.endsWith("/admin/llm-settings"))).toBe(true));
    expect(calls.find((c) => c.method === "PUT")?.body).toMatchObject({ provider: "groq", reason: "groq par shift", api_key: DUMMY_KEY });
    expect(await screen.findByText(t.llmSourceDashboard)).toBeInTheDocument();
  });

  it("does not send an empty api key", async () => {
    const calls = mockFetch((call) => {
      if (call.url.endsWith("/admin/llm-settings/test")) return { body: { ok: false, latency_ms: 5, error: "auth_error" } };
      if (call.url.endsWith("/admin/llm-settings")) return { body: LLM_DEFAULT };
      if (call.url.endsWith("/admin/global-pause")) return { body: { status: "running" } };
      if (call.url.endsWith("/admin/llm-usage")) return { body: [] };
      return { body: { items: [], next_cursor: null } };
    });
    renderApp(<AdminOps />, "/admin/ops");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: t.llmTest }));
    expect(await screen.findByText(/auth_error/)).toBeInTheDocument();
    expect(calls.find((c) => c.url.endsWith("/admin/llm-settings/test"))?.body).not.toHaveProperty("api_key");
  });
});

function AsOwner({ children }: { children: React.ReactNode }) {
  const { user, login } = useAuth();
  useEffect(() => {
    void login("o@brand.example", "correct horse battery", "brand");
  }, [login]);
  return user ? <>{children}</> : null;
}

const ROW = {
  id: "ac1", conversation_id: "c1", tool: "order_cancel", order_ref: "#1001", amount_paise: 129900,
  status: "pending_approval", requires_approval: true, risk_tier: "medium", reason: null, error: null,
  external_ref: null, created_at: "2026-01-01T00:00:00Z", expires_at: null, approved_at: null,
  sla_due_at: "2026-01-01T01:00:00Z", overdue: true,
};
const KILLS = ["address_update", "order_cancel", "exchange_request", "refund_create"].map((tool) => ({ tool, killed: false, reason: null, at: null }));

describe("approvals", () => {
  it("owner approves a pending action and sees the overdue chip", async () => {
    const calls = mockFetch((call) => {
      if (call.url.endsWith("/auth/login")) return { body: LOGIN_OK };
      if (call.url.endsWith("/tools/kill")) return { body: KILLS };
      if (call.url.endsWith("/approve")) return { body: { action_id: "ac1", status: "executed", detail: null } };
      if (call.url.includes("/approvals")) return { body: [ROW] };
      return undefined;
    });
    renderApp(<AsOwner><Approvals /></AsOwner>);
    const user = userEvent.setup();
    expect(await screen.findByText("₹1,299.00")).toBeInTheDocument();
    expect(screen.getByText(t.overdue)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: t.approve }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/approvals/ac1/approve") && c.method === "POST")).toBe(true));
  });

  it("reject needs a reason", async () => {
    const calls = mockFetch((call) => {
      if (call.url.endsWith("/auth/login")) return { body: LOGIN_OK };
      if (call.url.endsWith("/tools/kill")) return { body: KILLS };
      if (call.url.endsWith("/reject")) return { body: { action_id: "ac1", status: "rejected", detail: null } };
      if (call.url.includes("/approvals")) return { body: [ROW] };
      return undefined;
    });
    renderApp(<AsOwner><Approvals /></AsOwner>);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: t.reject }));
    await user.type(screen.getByLabelText(t.reasonLabel), "customer ne mana kiya");
    await user.click(screen.getByRole("button", { name: t.confirm }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/approvals/ac1/reject"))?.body).toEqual({ reason: "customer ne mana kiya" }));
  });

  it("owner can kill a tool with a reason", async () => {
    const calls = mockFetch((call) => {
      if (call.url.endsWith("/auth/login")) return { body: LOGIN_OK };
      if (call.url.endsWith("/tools/kill")) return { body: KILLS };
      if (call.url.endsWith("/order_cancel/kill")) return { body: { tool: "order_cancel", killed: true, reason: "x", at: null } };
      if (call.url.includes("/approvals")) return { body: [] };
      return undefined;
    });
    renderApp(<AsOwner><Approvals /></AsOwner>);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: `${t.killButton}: ${t.tool_order_cancel}` }));
    await user.type(screen.getByLabelText(t.reasonLabel), "galat cancel ho rahe");
    await user.click(screen.getByRole("button", { name: t.confirm }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/tools/order_cancel/kill") && c.method === "POST")).toBe(true));
  });

  it("without an owner session there are no action buttons", async () => {
    mockFetch((call) => {
      if (call.url.endsWith("/tools/kill")) return { body: KILLS };
      if (call.url.includes("/approvals")) return { body: [ROW] };
      return undefined;
    });
    renderApp(<Approvals />);
    expect(await screen.findByText("#1001")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: t.approve })).toBeNull();
  });
});

describe("autonomy settings", () => {
  const content = {
    schema_version: 2, office_hours: { enabled: false, days: [], start: "09:00", end: "18:00" }, return_window_days: 7,
    final_sale_tags: [], customer_daily_msg_cap: 30, brand_daily_msg_cap: 2000, handoff_sla_minutes: 30,
    ai_disclosure: { enabled: true, text: "AI" }, extra_handoff_keywords: [], enabled_tools: ["handoff_to_human", "order_lookup", "tracking_lookup"],
    autonomy: { order_status: "auto", address_update: "off", order_cancel: "off", exchange_request: "off", refund_create: "off" },
    limits: { cancel_auto_limit_paise: 200000, exchange_auto_limit_paise: 200000, refund_auto_limit_paise: 50000, refund_customer_daily_paise: 100000, brand_daily_auto_value_paise: 2000000, high_value_threshold_paise: 500000 },
  };

  it("saves approval level through draft, regression and publish", async () => {
    const policy = { id: "p1", version: 3, status: "published", content, created_at: "2026-01-01T00:00:00Z", published_at: "2026-01-01T00:00:00Z", regression_run_id: null };
    const calls = mockFetch((call) => {
      if (call.method === "GET" && call.url.endsWith("/policies")) return { body: [policy] };
      if (call.method === "POST" && call.url.endsWith("/policies")) return { status: 201, body: { ...policy, id: "p4", version: 4, status: "draft" } };
      if (call.url.endsWith("/p4/regression")) return { body: { run_id: "r1", passed: true, scenarios_total: 1, scenarios_failed: [], eval_subset_passed: true } };
      if (call.url.endsWith("/p4/publish")) return { body: { ...policy, id: "p4", version: 4 } };
      return undefined;
    });
    renderApp(<AutonomySettings />);
    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText(t.tool_order_cancel), "approval");
    await user.click(screen.getByRole("button", { name: t.autonomySave }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/p4/publish"))).toBe(true));
    const create = calls.find((c) => c.method === "POST" && c.url.endsWith("/policies"))?.body as { base_version: number; content: { autonomy: Record<string, string>; enabled_tools: string[] } };
    expect(create.base_version).toBe(3);
    expect(create.content.autonomy.order_cancel).toBe("approval");
    expect(create.content.enabled_tools).toContain("order_cancel");
  });

  it("offers the V2 upgrade for a V1 policy", async () => {
    const v1 = { schema_version: 1, return_window_days: 7, final_sale_tags: [], refund_auto_limit_paise: null, refund_customer_daily_limit_paise: null, enabled_tools: ["order_lookup"] };
    mockFetch((call) => {
      if (call.method === "GET" && call.url.endsWith("/policies")) return { body: [{ id: "p1", version: 1, status: "published", content: v1, created_at: "2026-01-01T00:00:00Z", published_at: null, regression_run_id: null }] };
      return undefined;
    });
    renderApp(<AutonomySettings />);
    expect(await screen.findByRole("button", { name: t.autonomyUpgrade })).toBeInTheDocument();
  });
});

describe("voice settings", () => {
  it("shows a read-only status and lists calls with transcript", async () => {
    const settings = {
      configured: true,
      called_number: "+911140000000",
      transfer_number: null,
      enabled: true,
      webhook_url: "https://api.example/webhooks/voice/abc",
      provider: "generic",
      providers: ["fake_ivr", "generic"],
      telephony_mode: "ready_made",
    };
    const call = { id: "c1", status: "callback", caller_masked: "+91******3210", turn_count: 2, end_reason: "handoff", started_at: new Date().toISOString(), ended_at: null, conversation_id: "v1", order_ref: "#1001", avg_latency_ms: 900, max_latency_ms: 1500, slow_turns: 0, feedback: null, feedback_note: null, summary_status: "skipped" };
    const stats = { days: 7, calls: 1, handled_by_agent: 0, transferred: 0, callback: 1, failed: 0, avg_turns: 2, avg_latency_ms: 900, slow_turns: 0, feedback_good: 0, feedback_bad: 0, agent_rate_percent: 0 };
    const calls = mockFetch((c) => {
      if (c.method === "GET" && c.url.endsWith("/voice/settings")) return { body: settings };
      if (c.url.includes("/voice/stats")) return { body: stats };
      if (c.method === "POST" && c.url.endsWith("/voice/calls/c1/feedback")) return { body: { ...call, feedback: "good" } };
      if (c.url.endsWith("/voice/calls")) return { body: [call] };
      if (c.url.endsWith("/voice/calls/c1")) return { body: { ...call, turns: [{ at: "2026-01-01T10:00:01Z", speaker: "caller", text: "order kahan hai" }] } };
      return undefined;
    });
    const user = userEvent.setup();
    renderApp(<VoiceSettings />);
    expect(await screen.findByText(/\+911140000000/)).toBeInTheDocument();
    expect(screen.getByText(t.voiceStatusConfigured)).toBeInTheDocument();
    await user.click(await screen.findByText(/3210/));
    expect(await screen.findByText(/order kahan hai/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: t.voiceFeedbackGood }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/voice/calls/c1/feedback"))).toBe(true));
    expect(calls.find((c) => c.url.endsWith("/feedback"))?.body).toEqual({ verdict: "good" });
  });
});
