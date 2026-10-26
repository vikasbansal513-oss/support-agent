import { fireEvent, screen, waitFor } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { isPositiveDecimal, paiseToInput, previousMonthIst } from "../billing";
import { AdminBilling, AdminInvoice } from "../pages/Billing";
import { mockFetch, renderApp } from "./helpers";

const ID = "11111111-1111-1111-1111-111111111111";
const TID = "22222222-2222-2222-2222-222222222222";

const PERIOD = {
  period: "2026-09",
  period_ended: true,
  usd_inr: "88.0000",
  cloudrun_actual_paise: null,
  cloudrun_unallocated_paise: 0,
  rows: [
    {
      tenant_id: TID,
      name: "Acme",
      slug: "acme",
      calls: 120,
      input_tokens: 1000,
      output_tokens: 500,
      llm_paise: 12345,
      compute_seconds: 3600,
      jobs: 4,
      cloudrun_paise: 500,
      default_platform_fee_paise: 100000,
      unpriced_models: [],
      invoice: null,
    },
  ],
};

const INVOICE = {
  id: ID,
  tenant_id: TID,
  tenant_name: "Acme",
  period: "2026-09",
  period_start: "2026-09-01",
  status: "draft",
  doc_type: "proforma",
  invoice_no: null,
  invoice_date: null,
  usd_inr: "88.0000",
  cloudrun_basis: "estimate",
  llm_paise: 10000,
  cloudrun_paise: 0,
  platform_fee_paise: 90000,
  subtotal_paise: 100000,
  gst_rate_percent: "18.00",
  gst_kind: "intra",
  cgst_paise: 9000,
  sgst_paise: 9000,
  igst_paise: 0,
  total_paise: 118000,
  seller: { name: "Me", address: "", state_code: "27", gstin: "", email: "", phone: "", payment_details: "", sac_code: "" },
  buyer: { name: "Acme", address: "", state_code: "27", gstin: "", email: "" },
  lines: [{ position: 1, kind: "platform_fee", description: "Platform fee", quantity: "1", unit: "month", amount_paise: 90000, detail: {} }],
  blockers: [],
  warnings: [],
  generated_at: "2026-10-01T00:00:00Z",
  finalized_at: null,
};

afterEach(() => vi.unstubAllGlobals());

describe("billing helpers", () => {
  it("formats paise and validates decimals", () => {
    expect(paiseToInput(123456)).toBe("1234.56");
    expect(isPositiveDecimal("88.5")).toBe(true);
    expect(isPositiveDecimal("0")).toBe(false);
    expect(isPositiveDecimal("abc")).toBe(false);
    expect(previousMonthIst()).toMatch(/^\d{4}-\d{2}$/);
  });
});

describe("billing pages", () => {
  it("saves the FX rate for the period", async () => {
    const calls = mockFetch((c) => {
      if (c.url.includes("/admin/billing/periods/") && c.method === "GET") return { body: PERIOD };
      if (c.url.includes("/admin/billing/periods/") && c.method === "PUT") return { body: PERIOD };
    });
    renderApp(<AdminBilling />, "/admin/billing");
    const fx = await screen.findByLabelText(/USD/i);
    fireEvent.change(fx, { target: { value: "90" } });
    fireEvent.click(screen.getAllByRole("button").find((b) => b.className !== "secondary" && b.textContent !== "")!);
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.body).toEqual({ usd_inr: "90", cloudrun_actual_paise: null });
  });

  it("finalizes with the displayed total", async () => {
    const calls = mockFetch((c) => {
      if (c.url.endsWith(`/invoices/${ID}`) && c.method === "GET") return { body: INVOICE };
      if (c.url.endsWith("/finalize")) return { body: { ...INVOICE, status: "final", invoice_no: "INV/2026-27/0001" } };
    });
    renderApp(
      <Routes>
        <Route path="/admin/billing/invoices/:id" element={<AdminInvoice />} />
      </Routes>,
      `/admin/billing/invoices/${ID}`,
    );
    await screen.findByLabelText("invoice");
    const finalizeButtons = screen.getAllByRole("button").filter((b) => /final/i.test(b.textContent ?? ""));
    fireEvent.click(finalizeButtons[0]!);
    const dialog = await screen.findByRole("dialog");
    const confirm = dialog.querySelector("button")!;
    fireEvent.click(confirm);
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/finalize"))).toBe(true));
    expect(calls.find((c) => c.url.endsWith("/finalize"))!.body).toEqual({ expected_total_paise: 118000 });
  });
});
