import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, applyLogin, clearSession, setSessionLostHandler } from "../api/client";
import { LOGIN_OK, mockFetch } from "./helpers";

afterEach(() => {
  clearSession();
  setSessionLostHandler(null);
  vi.unstubAllGlobals();
});

describe("api client", () => {
  it("sends bearer token and csrf only on state changing calls", async () => {
    applyLogin(LOGIN_OK, "brand");
    const calls = mockFetch(() => ({ body: { ok: true } }));
    await api.get("/usage");
    await api.post("/agent/pause", { reason: "testing" });
    expect(calls[0]?.headers.get("Authorization")).toBe("Bearer tok1");
    expect(calls[0]?.headers.get("X-CSRF-Token")).toBeNull();
    expect(calls[1]?.headers.get("X-CSRF-Token")).toBe("csrf1");
  });

  it("parses the error body into ApiError with error_id", async () => {
    mockFetch(() => ({ status: 409, body: { code: "E-ACT-001", message: "Pehle hi faisla ho chuka hai.", error_id: "ERR-ABCDEF123456", severity: "warning" } }));
    const error = await api.get("/actions").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe("E-ACT-001");
    expect((error as ApiError).errorId).toBe("ERR-ABCDEF123456");
  });

  it("refreshes once on E-AUTH-004 and retries with the new token", async () => {
    applyLogin(LOGIN_OK, "brand");
    let first = true;
    const calls = mockFetch((call) => {
      if (call.url.endsWith("/auth/refresh")) return { body: { ...LOGIN_OK, access_token: "tok2", csrf_token: "csrf2" } };
      if (first) {
        first = false;
        return { status: 401, body: { code: "E-AUTH-004", message: "Session khatam", error_id: "ERR-000000000001" } };
      }
      return { body: { fine: true } };
    });
    await expect(api.get("/usage")).resolves.toEqual({ fine: true });
    expect(calls.map((c) => c.url.split("/").slice(-1)[0])).toEqual(["usage", "refresh", "usage"]);
    expect(calls[1]?.headers.get("X-CSRF-Token")).toBe("csrf1");
    expect(calls[2]?.headers.get("Authorization")).toBe("Bearer tok2");
  });

  it("calls the session-lost handler when refresh fails", async () => {
    applyLogin(LOGIN_OK, "brand");
    const lost = vi.fn();
    setSessionLostHandler(lost);
    mockFetch((call) =>
      call.url.endsWith("/auth/refresh")
        ? { status: 401, body: { code: "E-AUTH-004", message: "x", error_id: "ERR-000000000002" } }
        : { status: 401, body: { code: "E-AUTH-004", message: "x", error_id: "ERR-000000000003" } },
    );
    await expect(api.get("/usage")).rejects.toBeInstanceOf(ApiError);
    expect(lost).toHaveBeenCalledOnce();
  });

  it("uses the admin refresh endpoint for the admin realm", async () => {
    applyLogin(LOGIN_OK, "admin");
    let first = true;
    const calls = mockFetch((call) => {
      if (call.url.includes("/admin/auth/refresh")) return { body: LOGIN_OK };
      if (first) {
        first = false;
        return { status: 401, body: { code: "E-AUTH-004", message: "x", error_id: "ERR-000000000004" } };
      }
      return { body: {} };
    });
    await api.get("/admin/tenants");
    expect(calls.some((c) => c.url.includes("/admin/auth/refresh"))).toBe(true);
  });
});
