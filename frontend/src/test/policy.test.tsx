import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { clearSession } from "../api/client";
import { PolicySettings } from "../pages/Policy";
import { mockFetch, renderApp } from "./helpers";
import { t } from "../i18n";

afterEach(() => {
  clearSession();
  vi.unstubAllGlobals();
});

const content = {
  schema_version: 2,
  office_hours: { start: "09:00", end: "21:00", days: [0, 1, 2, 3, 4, 5, 6] },
  return_window_days: 7,
  final_sale_tags: [],
  customer_daily_msg_cap: 30,
  brand_daily_msg_cap: 2000,
  handoff_sla_minutes: 30,
  ai_disclosure: { hi: "Main ek AI sahayak hoon.", en: "I am an AI assistant.", hinglish: "Main ek AI hoon." },
  extra_handoff_keywords: [],
  enabled_tools: ["handoff_to_human", "order_lookup", "tracking_lookup"],
  autonomy: { order_status: "auto", address_update: "off", order_cancel: "off", exchange_request: "off", return_request: "off", refund_create: "off" },
};

function draft(extra: Record<string, unknown> = {}) {
  return { id: "d1", version: 2, status: "draft", content: { ...content, ...extra }, created_at: "2026-01-01T00:00:00Z", published_at: null, regression_run_id: null };
}

describe("policy settings", () => {
  it("saves the new group fields only when the brand set them", async () => {
    const calls = mockFetch((call) => {
      if (call.method === "GET" && call.url.endsWith("/policies")) return { body: [draft()] };
      if (call.method === "PUT") return { body: draft() };
      return undefined;
    });
    renderApp(<PolicySettings />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: t.save }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const sent = (calls.find((c) => c.method === "PUT")?.body as { content: Record<string, unknown> }).content;
    for (const key of ["holidays", "day_hours", "return_window_by_tag", "tone_notes", "confirm_ttl_minutes"]) {
      expect(sent).not.toHaveProperty(key);
    }
  });

  it("sends holidays, day hours and tag windows", async () => {
    const calls = mockFetch((call) => {
      if (call.method === "GET" && call.url.endsWith("/policies")) return { body: [draft()] };
      if (call.method === "PUT") return { body: draft() };
      return undefined;
    });
    renderApp(<PolicySettings />);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText(t.holidaysLabel), "2026-11-12, 2026-11-14");
    await user.type(screen.getByLabelText("Sat start"), "10:00");
    await user.type(screen.getByLabelText("Sat end"), "14:00");
    await user.type(screen.getByLabelText(t.tagWindows), "shoes=15, sale=3");
    await user.click(screen.getByRole("button", { name: t.save }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const sent = (calls.find((c) => c.method === "PUT")?.body as { content: Record<string, unknown> }).content;
    expect(sent.holidays).toEqual(["2026-11-12", "2026-11-14"]);
    expect(sent.day_hours).toEqual({ "5": { start: "10:00", end: "14:00" } });
    expect(sent.return_window_by_tag).toEqual({ shoes: 15, sale: 3 });
  });

  it("blocks a half-filled day and a bad tag window without calling the API", async () => {
    const calls = mockFetch((call) => {
      if (call.method === "GET" && call.url.endsWith("/policies")) return { body: [draft()] };
      return undefined;
    });
    renderApp(<PolicySettings />);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Mon start"), "10:00");
    await user.click(screen.getByRole("button", { name: t.save }));
    expect(await screen.findByText(t.dayHoursIncomplete)).toBeInTheDocument();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("locks safety dials while an action is on auto", async () => {
    mockFetch((call) => {
      if (call.method === "GET" && call.url.endsWith("/policies")) {
        return { body: [draft({ autonomy: { ...content.autonomy, order_cancel: "auto" } })] };
      }
      return undefined;
    });
    renderApp(<PolicySettings />);
    expect(await screen.findByText(t.safetyLocked)).toBeInTheDocument();
    expect(screen.getByLabelText(t.sentimentMin)).toBeDisabled();
  });
});
