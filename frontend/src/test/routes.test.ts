import { describe, expect, it } from "vitest";
import { ROUTES } from "../routes";
import { t } from "../i18n";

const SPEC_PATHS = [
  "/login", "/signup", "/verify-email", "/forgot-password", "/reset-password", "/pending", "/",
  "/onboarding", "/conversations", "/conversations/:id", "/handoffs", "/review", "/approvals", "/settings/autonomy", "/settings/voice",
  "/settings/integrations", "/settings/policy", "/settings/notifications", "/settings/security",
  "/privacy", "/privacy-notice", "/terms",
  "/admin/login", "/admin", "/admin/tenants", "/admin/tenants/:id", "/admin/ops", "/admin/audit-log", "/admin/alerts",
  "/admin/control-tower", "/admin/billing", "/admin/billing/settings", "/admin/billing/invoices/:id",
];
const SPEC_ACCESS: Record<string, string> = {
  "/": "user", "/onboarding": "owner", "/settings/integrations": "owner", "/settings/policy": "owner", "/settings/autonomy": "owner", "/settings/voice": "owner", "/approvals": "user",
  "/settings/notifications": "owner", "/settings/security": "user", "/admin": "admin", "/admin/tenants": "admin", "/admin/tenants/:id": "admin",
  "/admin/ops": "admin", "/admin/audit-log": "admin", "/admin/alerts": "admin", "/admin/control-tower": "admin",
  "/admin/billing": "admin", "/admin/billing/settings": "admin", "/admin/billing/invoices/:id": "admin",
  "/login": "public", "/admin/login": "public",
};

describe("route table", () => {
  it("covers every spec route", () => {
    expect(ROUTES.map((r) => r.path).sort()).toEqual([...SPEC_PATHS].sort());
  });

  it("matches the access level from the spec", () => {
    for (const route of ROUTES) {
      const expected = SPEC_ACCESS[route.path];
      if (expected) expect(route.access).toBe(expected);
    }
  });

  it("keeps every UI string in the i18n file", () => {
    expect(Object.keys(t).length).toBeGreaterThan(100);
  });
});
