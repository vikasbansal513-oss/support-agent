import type { LoginOut, Realm } from "./types";

export class ApiError extends Error {
  readonly code: string;
  readonly errorId: string | null;
  readonly severity: "critical" | "error" | "warning";
  readonly status: number;

  constructor(status: number, code: string, message: string, errorId: string | null, severity: "critical" | "error" | "warning") {
    super(message);
    this.status = status;
    this.code = code;
    this.errorId = errorId;
    this.severity = severity;
  }
}

interface Session {
  accessToken: string | null;
  csrf: string | null;
  realm: Realm;
}

const session: Session = { accessToken: null, csrf: null, realm: "brand" };
let onSessionLost: (() => void) | null = null;
let refreshing: Promise<boolean> | null = null;

export function baseUrl(): string {
  const value = import.meta.env.VITE_API_BASE_URL as string | undefined;
  return (value ?? "").replace(/\/$/, "");
}

export function setSessionLostHandler(handler: (() => void) | null): void {
  onSessionLost = handler;
}

export function applyLogin(out: LoginOut, realm: Realm): void {
  session.accessToken = out.access_token;
  session.csrf = out.csrf_token;
  session.realm = realm;
}

export function clearSession(): void {
  session.accessToken = null;
  session.csrf = null;
}

export function hasSession(): boolean {
  return session.accessToken !== null;
}

function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  const value = match?.[1];
  return value !== undefined ? decodeURIComponent(value) : null;
}

function csrfCookieName(realm: Realm): string {
  return realm === "admin" ? "sa_admin_csrf" : "sa_csrf";
}

async function parseError(response: Response): Promise<ApiError> {
  let body: Record<string, unknown> = {};
  try {
    body = (await response.json()) as Record<string, unknown>;
  } catch {
    body = {};
  }
  const code = typeof body.code === "string" ? body.code : "E-SYS-001";
  const message = typeof body.message === "string" ? body.message : "Kuch gadbad hui. Dobara try karein.";
  const errorId = typeof body.error_id === "string" ? body.error_id : null;
  const severity = body.severity === "critical" || body.severity === "warning" ? body.severity : "error";
  return new ApiError(response.status, code, message, errorId, severity);
}

function headersFor(method: string, hasBody: boolean, withAuth: boolean): Headers {
  const headers = new Headers();
  if (hasBody) headers.set("Content-Type", "application/json");
  if (withAuth && session.accessToken) headers.set("Authorization", `Bearer ${session.accessToken}`);
  if (method !== "GET" && session.csrf) headers.set("X-CSRF-Token", session.csrf);
  return headers;
}

async function refresh(): Promise<boolean> {
  if (refreshing) return refreshing;
  const path = session.realm === "admin" ? "/admin/auth/refresh" : "/auth/refresh";
  refreshing = (async () => {
    try {
      const response = await fetch(baseUrl() + path, {
        method: "POST",
        credentials: "include",
        headers: headersFor("POST", false, false),
      });
      if (!response.ok) return false;
      applyLogin((await response.json()) as LoginOut, session.realm);
      return true;
    } catch {
      return false;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

/**
 * Called once on app startup (before any route renders) to restore a
 * session from the httponly refresh cookie, since the access token only
 * lives in JS memory and is lost on every full page load/reload (e.g. the
 * Gmail OAuth redirect round-trip). In-memory state is empty at this point,
 * so the CSRF header is read from the readable sa_csrf/sa_admin_csrf
 * cookie instead of `session.csrf`.
 */
export async function restoreSession(realm: Realm): Promise<LoginOut | null> {
  const path = realm === "admin" ? "/admin/auth/refresh" : "/auth/refresh";
  try {
    const headers = new Headers();
    const csrf = readCookie(csrfCookieName(realm));
    if (csrf) headers.set("X-CSRF-Token", csrf);
    const response = await fetch(baseUrl() + path, {
      method: "POST",
      credentials: "include",
      headers,
    });
    if (!response.ok) return null;
    const out = (await response.json()) as LoginOut;
    applyLogin(out, realm);
    return out;
  } catch {
    return null;
  }
}

export async function request<T>(method: string, path: string, body?: unknown, retry = true): Promise<T> {
  const response = await fetch(baseUrl() + path, {
    method,
    credentials: "include",
    headers: headersFor(method, body !== undefined, true),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.ok) {
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }
  const error = await parseError(response);
  if (error.code === "E-AUTH-004" && retry) {
    if (await refresh()) return request<T>(method, path, body, false);
    clearSession();
    onSessionLost?.();
  }
  throw error;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  delete: <T>(path: string) => request<T>("DELETE", path),
};

export async function downloadFile(path: string, filename: string): Promise<void> {
  const response = await fetch(baseUrl() + path, {
    method: "GET",
    credentials: "include",
    headers: headersFor("GET", false, true),
  });
  if (!response.ok) throw await parseError(response);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export async function publicPost<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(baseUrl() + path, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw await parseError(response);
  return (await response.json()) as T;
}

export async function logoutRequest(): Promise<void> {
  const path = session.realm === "admin" ? "/admin/auth/logout" : "/auth/logout";
  try {
    await fetch(baseUrl() + path, {
      method: "POST",
      credentials: "include",
      headers: headersFor("POST", false, false),
    });
  } finally {
    clearSession();
  }
}
