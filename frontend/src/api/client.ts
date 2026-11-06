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

// Session-restore calls are on the critical path for first paint (every full
// page load/refresh waits on one of these before the dashboard can render),
// so they're bounded by a timeout instead of being able to hang indefinitely
// on a stalled connection.
const AUTH_TIMEOUT_MS = 10_000;

async function fetchWithTimeout(url: string, init: RequestInit, timeoutMs: number): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

// De-dupes identical concurrent GETs (e.g. two components mounting at once
// and requesting the same resource) into a single network request; callers
// still each get their own copy of the resolved value.
const inflightGets = new Map<string, Promise<unknown>>();

// Section 2 (Speed & Scaling): a short in-process TTL cache for completed GETs,
// mirroring the same "in-process TTL cache, invalidate on write" pattern used
// for the backend's policy-settings cache. Re-visiting a page you were just on
// (switching tabs, navigating back) is extremely common in this dashboard and
// previously re-fetched every field from scratch every time. The TTL is short
// (GET_CACHE_TTL_MS) precisely because this is a live support dashboard, not a
// place to risk showing stale conversation/approval state - it only helps the
// "clicked back within a few seconds" case, not general polling.
//
// Invalidation is deliberately coarse: any non-GET request clears the whole
// cache rather than trying to track which GET paths a given mutation affects.
// Guessing that relationship wrong (e.g. missing that approving a handoff also
// changes the dashboard's open-handoff count) would show stale data after an
// action, which is worse than the small extra fetches a full clear costs.
const GET_CACHE_TTL_MS = 15_000;
const getCache = new Map<string, { data: unknown; expiresAt: number }>();

function cacheGet<T>(path: string): T | undefined {
  const entry = getCache.get(path);
  if (!entry) return undefined;
  if (entry.expiresAt < Date.now()) {
    getCache.delete(path);
    return undefined;
  }
  return entry.data as T;
}

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
  // Logging out (or a different tenant/admin logging in on the same tab)
  // must not leave the previous session's cached GET responses readable.
  getCache.clear();
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
  const message = typeof body.message === "string" ? body.message : "Something went wrong. Please try again.";
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
      const response = await fetchWithTimeout(
        baseUrl() + path,
        { method: "POST", credentials: "include", headers: headersFor("POST", false, false) },
        AUTH_TIMEOUT_MS,
      );
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
export async function restoreSession(realm: Realm, activate = true): Promise<LoginOut | null> {
  const path = realm === "admin" ? "/admin/auth/refresh" : "/auth/refresh";
  try {
    const headers = new Headers();
    const csrf = readCookie(csrfCookieName(realm));
    if (csrf) headers.set("X-CSRF-Token", csrf);
    const response = await fetchWithTimeout(
      baseUrl() + path,
      { method: "POST", credentials: "include", headers },
      AUTH_TIMEOUT_MS,
    );
    if (!response.ok) return null;
    const out = (await response.json()) as LoginOut;
    if (activate) applyLogin(out, realm);
    return out;
  } catch {
    return null;
  }
}

async function doRequest<T>(method: string, path: string, body: unknown, retry: boolean): Promise<T> {
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
    if (await refresh()) return doRequest<T>(method, path, body, false);
    clearSession();
    onSessionLost?.();
  }
  throw error;
}

export async function request<T>(method: string, path: string, body?: unknown, retry = true): Promise<T> {
  // GET requests have no side effects, so identical concurrent calls (e.g.
  // two components mounting at once and asking for the same resource) are
  // collapsed into one network request instead of firing one each.
  if (method === "GET") {
    const cached = cacheGet<T>(path);
    if (cached !== undefined) return cached;
    const existing = inflightGets.get(path);
    if (existing) return existing as Promise<T>;
    const promise = doRequest<T>(method, path, body, retry)
      .then((data) => {
        getCache.set(path, { data, expiresAt: Date.now() + GET_CACHE_TTL_MS });
        return data;
      })
      .finally(() => {
        inflightGets.delete(path);
      });
    inflightGets.set(path, promise);
    return promise;
  }
  // A write may change what any previously-cached GET would return, and
  // tracking exactly which paths each mutation affects is error-prone (see
  // the comment on getCache above), so every write simply clears the cache.
  getCache.clear();
  return doRequest<T>(method, path, body, retry);
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
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Revoking the object URL immediately after click() can cut the download
  // off in some browsers (the download is handed off asynchronously, not
  // completed by the time click() returns), so free it on the next tick
  // instead of synchronously.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
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
