import { useState, type FormEvent, type ReactNode } from "react";
import type { Loadable } from "./useApi";
import { ApiError } from "../api/client";
import { t } from "../i18n";
import { Icon } from "./Icons";

export function Screen({
  title,
  subtitle,
  actions,
  children,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section>
      <div className="page-head">
        <div>
          <h1>{title}</h1>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {actions && <div className="head-actions">{actions}</div>}
      </div>
      {children}
    </section>
  );
}

const STAT_ICON_TONE: Record<"warn" | "ok" | "purple" | "default", string> = {
  warn: "orange",
  ok: "green",
  purple: "purple",
  default: "blue",
};

export function StatCard({
  label,
  value,
  tone,
  icon = "info",
  trend,
}: {
  label: string;
  value: ReactNode;
  tone?: "warn" | "ok" | "purple";
  icon?: string;
  trend?: { direction: "up" | "down"; text: string };
}) {
  return (
    <div className="stat-card">
      <div className={`stat-icon ${STAT_ICON_TONE[tone ?? "default"]}`}>
        <Icon name={icon} />
      </div>
      <div>
        <div className="stat-number">{value}</div>
        <div className="stat-label">{label}</div>
        {trend && <div className={`stat-trend ${trend.direction}`}>{trend.direction === "up" ? "↑" : "↓"} {trend.text}</div>}
      </div>
    </div>
  );
}

export function Chip({ children, tone }: { children: ReactNode; tone?: "warn" | "ok" | "urgent" }) {
  const color = tone === "urgent" ? "red" : tone === "ok" ? "green" : tone === "warn" ? "amber" : "gray";
  return <span className={`pill ${color}`}>{children}</span>;
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="card empty-state">
      <div className="empty-icon">
        <Icon name="info" />
      </div>
      <p>{children}</p>
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  const apiError = error instanceof ApiError ? error : null;
  return (
    <div className="error-box" role="alert">
      <p>{error.message}</p>
      {apiError?.errorId && (
        <small>
          {t.errorIdLabel}: {apiError.errorId}
        </small>
      )}
      {onRetry && (
        <button type="button" onClick={onRetry}>
          {t.retry}
        </button>
      )}
    </div>
  );
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="skeleton-block" role="status" aria-label={t.loading}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton-row" />
      ))}
    </div>
  );
}

export function PageSkeleton() {
  return (
    <div className="skeleton-page" role="status" aria-label={t.loading}>
      <div className="skeleton-row skeleton-title" />
      <div className="stats">
        <div className="skeleton-card" />
        <div className="skeleton-card" />
        <div className="skeleton-card" />
        <div className="skeleton-card" />
      </div>
      <Skeleton rows={4} />
    </div>
  );
}

// Full-page loading state shown only while the very first session-restore call is in
// flight (AuthContext's `loading`), before we even know whether there is a logged-in
// user - distinct from PageSkeleton, which shows the shape of a page we already know is
// coming (an authenticated route's chunk loading). Replaces the earlier blank screen.
export function AuthLoadingScreen() {
  return (
    <div className="auth-loading" role="status" aria-live="polite">
      <span className="brand-mark">
        <Icon name="bot" />
      </span>
      <p>{t.loadingDashboard}</p>
    </div>
  );
}

export function Async<T>({
  value,
  reload,
  children,
  skeleton,
}: {
  value: Loadable<T>;
  reload?: () => void;
  children: (data: T) => ReactNode;
  skeleton?: ReactNode;
}) {
  if (value.state === "loading") return <>{skeleton ?? <Skeleton />}</>;
  if (value.state === "error") return <ErrorBox error={value.error} onRetry={reload} />;
  return <>{children(value.data)}</>;
}

export function Field({
  label,
  value,
  onChange,
  type = "text",
  autoComplete,
  required,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  autoComplete?: string;
  required?: boolean;
  placeholder?: string;
}) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      <input
        className="input"
        type={type}
        value={value}
        autoComplete={autoComplete}
        required={required}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

export function ReasonDialog({
  title,
  onConfirm,
  onCancel,
}: {
  title: string;
  onConfirm: (reason: string) => void;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (reason.trim().length >= 5) onConfirm(reason.trim());
  };
  return (
    <div className="dialog" role="dialog" aria-modal="true" aria-label={title}>
      <form className="card" onSubmit={submit}>
        <h2>{title}</h2>
        <Field label={t.reasonLabel} value={reason} onChange={setReason} required />
        <div className="row">
          <button type="submit" disabled={reason.trim().length < 5}>
            {t.confirm}
          </button>
          <button type="button" className="secondary" onClick={onCancel}>
            {t.cancel}
          </button>
        </div>
      </form>
    </div>
  );
}

export function useSubmit(action: () => Promise<void>, onError: (err: unknown) => void) {
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      await action();
    } catch (err) {
      onError(err);
    } finally {
      setBusy(false);
    }
  };
  return { busy, run };
}
