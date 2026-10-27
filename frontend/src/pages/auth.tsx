import { useEffect, useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError, publicPost } from "../api/client";
import type { Realm } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Field } from "../components/common";
import { useToast } from "../components/Toast";
import { t } from "../i18n";

function LoginForm({ realm }: { realm: Realm }) {
  const { login, verifyMfa, user } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const home = realm === "admin" ? "/admin" : "/";

  if (user) return <Navigate to={home} replace />;

  const fail = (err: unknown) => {
    if (err instanceof ApiError && err.code === "E-AUTH-009") {
      navigate("/pending");
      return;
    }
    toast.error(err);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      if (mfaToken) {
        await verifyMfa(mfaToken, code, realm);
      } else {
        const challenge = await login(email, password, realm);
        if (challenge) setMfaToken(challenge.mfa_token);
      }
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth">
      <h1>{realm === "admin" ? t.adminLoginTitle : mfaToken ? t.mfaTitle : t.loginTitle}</h1>
      <form onSubmit={(event) => void submit(event)}>
        {mfaToken ? (
          <Field label={t.mfaCode} value={code} onChange={setCode} autoComplete="one-time-code" required />
        ) : (
          <>
            <Field label={t.email} type="email" value={email} onChange={setEmail} autoComplete="username" required />
            <Field label={t.password} type="password" value={password} onChange={setPassword} autoComplete="current-password" required />
          </>
        )}
        <button type="submit" disabled={busy}>
          {t.login}
        </button>
      </form>
      {realm === "brand" && !mfaToken && (
        <p>
          <Link to="/forgot-password">{t.forgot}</Link> · <Link to="/signup">{t.signup}</Link>
        </p>
      )}
    </div>
  );
}

export function Login() {
  return <LoginForm realm="brand" />;
}

export function AdminLogin() {
  return <LoginForm realm="admin" />;
}

export function Signup() {
  const toast = useToast();
  const [brandName, setBrandName] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      await publicPost("/auth/signup", { brand_name: brandName, name, email, password });
      setSent(true);
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(false);
    }
  };

  if (sent) return <div className="auth"><p>{t.signupSent}</p></div>;
  return (
    <div className="auth">
      <h1>{t.signupTitle}</h1>
      <form onSubmit={(event) => void submit(event)}>
        <Field label={t.brandName} value={brandName} onChange={setBrandName} required />
        <Field label={t.name} value={name} onChange={setName} required />
        <Field label={t.email} type="email" value={email} onChange={setEmail} autoComplete="username" required />
        <Field label={t.password} type="password" value={password} onChange={setPassword} autoComplete="new-password" required />
        <button type="submit" disabled={busy}>
          {t.signup}
        </button>
      </form>
      <p><Link to="/login">{t.login}</Link></p>
    </div>
  );
}

export function VerifyEmail() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [state, setState] = useState<"verifying" | "ok" | "expired">("verifying");
  const [email, setEmail] = useState("");
  const toast = useToast();

  useEffect(() => {
    let alive = true;
    publicPost("/auth/verify-email", { token }).then(
      () => alive && setState("ok"),
      () => alive && setState("expired"),
    );
    return () => {
      alive = false;
    };
  }, [token]);

  const resend = async () => {
    try {
      await publicPost("/auth/resend-verification", { email });
      toast.info(t.signupSent);
    } catch (err) {
      toast.error(err);
    }
  };

  return (
    <div className="auth">
      <h1>{t.verifyTitle}</h1>
      {state === "verifying" && <p className="loading">{t.loading}</p>}
      {state === "ok" && (
        <p>
          {t.verifyOk} <Link to="/login">{t.login}</Link>
        </p>
      )}
      {state === "expired" && (
        <div className="card">
          <p>{t.verifyExpired}</p>
          <Field label={t.email} type="email" value={email} onChange={setEmail} />
          <button type="button" onClick={() => void resend()}>
            {t.resend}
          </button>
        </div>
      )}
    </div>
  );
}

export function ForgotPassword() {
  const toast = useToast();
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await publicPost("/auth/forgot-password", { email });
      setSent(true);
    } catch (err) {
      toast.error(err);
    }
  };

  return (
    <div className="auth">
      <h1>{t.forgotTitle}</h1>
      {sent ? (
        <p>{t.forgotSent}</p>
      ) : (
        <form onSubmit={(event) => void submit(event)}>
          <Field label={t.email} type="email" value={email} onChange={setEmail} required />
          <button type="submit">{t.save}</button>
        </form>
      )}
    </div>
  );
}

export function ResetPassword() {
  const [params] = useSearchParams();
  const toast = useToast();
  const [password, setPassword] = useState("");
  const [done, setDone] = useState(false);
  const [expired, setExpired] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await publicPost("/auth/reset-password", { token: params.get("token") ?? "", new_password: password });
      setDone(true);
    } catch (err) {
      if (err instanceof ApiError && err.code === "E-AUTH-008") setExpired(true);
      else toast.error(err);
    }
  };

  return (
    <div className="auth">
      <h1>{t.resetTitle}</h1>
      {done && (
        <p>
          {t.saved}. <Link to="/login">{t.login}</Link>
        </p>
      )}
      {expired && <p>{t.verifyExpired}</p>}
      {!done && !expired && (
        <form onSubmit={(event) => void submit(event)}>
          <Field label={t.newPassword} type="password" value={password} onChange={setPassword} autoComplete="new-password" required />
          <button type="submit">{t.save}</button>
        </form>
      )}
    </div>
  );
}

export function Pending() {
  return (
    <div className="auth">
      <h1>{t.pendingTitle}</h1>
      <p>{t.pendingBody}</p>
      <Link to="/login">{t.login}</Link>
    </div>
  );
}
