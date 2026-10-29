import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
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
  const [user, setUser] = useState<User | null>(null);
  const [realm, setRealm] = useState<Realm>("brand");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setSessionLostHandler(() => setUser(null));
    return () => setSessionLostHandler(null);
  }, []);

  // Restore session from the httponly refresh cookie on every app startup
  // (first load, and every full page reload — e.g. coming back from the
  // Gmail OAuth redirect). The access token lives only in JS memory, so
  // without this the Guard sees user=null and bounces to /login before
  // this ever gets a chance to run via a failed request.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const brand = await restoreSession("brand");
      if (cancelled) return;
      if (brand) {
        setRealm("brand");
        setUser(brand.user);
        setLoading(false);
        return;
      }
      const admin = await restoreSession("admin");
      if (cancelled) return;
      if (admin) {
        setRealm("admin");
        setUser(admin.user);
      }
      setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

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
