import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import { ToastProvider } from "../components/Toast";

export interface Call {
  method: string;
  url: string;
  body: unknown;
  headers: Headers;
}

export type Handler = (call: Call) => { status?: number; body?: unknown } | undefined;

export function mockFetch(handler: Handler): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const call: Call = {
        method: init?.method ?? "GET",
        url: String(url),
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
        headers: new Headers(init?.headers),
      };
      calls.push(call);
      const result = handler(call) ?? { status: 404, body: { code: "E-NF-001", message: "Nahi mila.", error_id: "ERR-000000000000" } };
      return new Response(result.status === 204 ? null : JSON.stringify(result.body ?? {}), {
        status: result.status ?? 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  return calls;
}

export function renderApp(ui: ReactElement, path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ToastProvider>
        <AuthProvider>{ui}</AuthProvider>
      </ToastProvider>
    </MemoryRouter>,
  );
}

import type { LoginOut, User } from "../api/types";

export const OWNER: User = {
  id: "u1",
  email: "o@brand.example",
  name: "Owner",
  role: "owner",
  tenant_id: "t1",
  tenant_name: "Brand Co",
  mfa_ok: true,
};

export const LOGIN_OK: LoginOut = { access_token: "tok1", expires_in: 900, csrf_token: "csrf1", user: OWNER };
