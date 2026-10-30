import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { applyLogin, clearSession, logoutRequest, publicPost, restoreSession, setSessionLostHandler } from "../api/client";
import type { LoginOut, MfaChallenge, Realm, User } from "../api/types";

export interface AuthValue {
  user: User | null;
  realm: Realm;
  loading: boolean;
  login: (email: string, password: string, realm: Realm) => Promise<MfaChallenge | null>;
  verifyMfa: (mfaToken: string, code: string, realm: Realm) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

function isChallenge(value: LoginOut | MfaChallenge): value is MfaChallenge {
  return (value as MfaChallenge).mfa_required === true;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const location = useLocation();
  const requestedRealm: Realm = location.pathname === "/admin" || location.pathname.startsWith("/admin/")
    ? "admin"
    : "brand";
  const [user, setUser] = useState<User | null>(null);
  const [realm, setRealm] = useState<Realm>(requestedRealm);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setSessionLostHandler(() => setUser(null));
    return () => setSessionLostHandler(null);
  }, []);

  // Restore only the session realm requested by the current route. This keeps
  // an existing Brand cookie from taking precedence over Admin routes (and
  // vice versa), while retaining independent HttpOnly refresh cookies.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setUser(null);
    setRealm(requestedRealm);
    clearSession();

    (async () => {
      const restored = await restoreSession(requestedRealm, false);
      if (cancelled) return;
      if (restored) {
        applyLogin(restored, requestedRealm);
        setUser(restored.user);
      }
      setLoading(false);
    })();

    return () => {
      cancelled = true;
    };
  }, [requestedRealm]);

  const login = useCallback(async (email: string, password: string, target: Realm) => {
    const path = target === "admin" ? "/admin/auth/login" : "/auth/login";
    const out = await publicPost<LoginOut | MfaChallenge>(path, { email, password });
    if (isChallenge(out)) return out;
    applyLogin(out, target);
    setRealm(target);
    setUser(out.user);
    return null;
  }, []);

  const verifyMfa = useCallback(async (mfaToken: string, code: string, target: Realm) => {
    const path = target === "admin" ? "/admin/auth/mfa/verify" : "/auth/mfa/verify";
    const out = await publicPost<LoginOut>(path, { mfa_token: mfaToken, code });
    applyLogin(out, target);
    setRealm(target);
    setUser(out.user);
  }, []);

  const logout = useCallback(async () => {
    try {
      await logoutRequest();
    } finally {
      clearSession();
      setUser(null);
    }
  }, []);

  const value = useMemo(
    () => ({ user, realm, loading, login, verifyMfa, logout }),
    [user, realm, loading, login, verifyMfa, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("AuthProvider missing");
  return value;
}
