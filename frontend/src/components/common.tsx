import { useState, type FormEvent, type ReactNode } from "react";
import type { Loadable } from "./useApi";
import { ApiError } from "../api/client";
import { t } from "../i18n";

export function Screen({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="screen">
      <h1>{title}</h1>
      {children}
    </section>
  );
}

export function StatCard({ label, value, tone }: { label: string; value: ReactNode; tone?: "warn" | "ok" }) {
  return (
    <div className={tone ? `stat stat-${tone}` : "stat"}>
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}

export function Chip({ children, tone }: { children: ReactNode; tone?: "warn" | "ok" | "urgent" }) {
  return <span className={tone ? `chip chip-${tone}` : "chip"}>{children}</span>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
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

export function Async<T>({
  value,
  reload,
  children,
}: {
  value: Loadable<T>;
  reload?: () => void;
  children: (data: T) => ReactNode;
}) {
  if (value.state === "loading") return <p className="loading">{t.loading}</p>;
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
      <span>{label}</span>
      <input
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
      <form onSubmit={submit}>
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
