import QRCode from "qrcode";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { MfaStatus } from "../api/types";
import { Async, Field, Screen, useSubmit } from "../components/common";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { t } from "../i18n";

interface Setup {
  secret: string;
  otpauth_uri: string;
}
interface Codes {
  enabled: boolean;
  recovery_codes: string[];
}

export function QrBlock({ uri }: { uri: string }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    QRCode.toDataURL(uri).then(
      (value) => alive && setSrc(value),
      () => alive && setSrc(null),
    );
    return () => {
      alive = false;
    };
  }, [uri]);
  return src ? <img src={src} alt="MFA QR" width={200} height={200} /> : <code>{uri}</code>;
}

function MfaCard({ status, onChange }: { status: MfaStatus; onChange: () => void }) {
  const toast = useToast();
  const [setup, setSetup] = useState<Setup | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);

  const start = useSubmit(async () => {
    setSetup(await api.post<Setup>("/auth/mfa/setup"));
  }, toast.error);
  const confirm = useSubmit(async () => {
    const out = await api.post<Codes>("/auth/mfa/confirm", { code });
    setCodes(out.recovery_codes);
    setSetup(null);
    setCode("");
    onChange();
  }, toast.error);
  const regenerate = useSubmit(async () => {
    const out = await api.post<Codes>("/auth/mfa/recovery-codes", { code });
    setCodes(out.recovery_codes);
    setCode("");
    onChange();
  }, toast.error);

  return (
    <div className="card">
      <h2>{status.enabled ? t.mfaOn : t.mfaOff}</h2>
      {!status.enabled && !setup && (
        <button type="button" disabled={start.busy} onClick={() => void start.run()}>
          {t.mfaSetup}
        </button>
      )}
      {setup && (
        <>
          <p>{t.mfaScan}</p>
          <QrBlock uri={setup.otpauth_uri} />
          <Field label={t.mfaCode} value={code} onChange={setCode} autoComplete="one-time-code" />
          <button type="button" disabled={confirm.busy || code.length < 6} onClick={() => void confirm.run()}>
            {t.mfaConfirm}
          </button>
        </>
      )}
      {status.enabled && (
        <>
          <p>
            {t.recoveryRemaining}: {status.recovery_remaining}
          </p>
          <Field label={t.mfaCode} value={code} onChange={setCode} autoComplete="one-time-code" />
          <button type="button" className="secondary" disabled={regenerate.busy || code.length < 6} onClick={() => void regenerate.run()}>
            {t.newRecovery}
          </button>
        </>
      )}
      {codes && (
        <div className="banner" role="dialog" aria-label={t.recoveryCodes}>
          <strong>{t.recoveryCodes}</strong>
          <pre>{codes.join("\n")}</pre>
          <button type="button" onClick={() => setCodes(null)}>
            {t.confirm}
          </button>
        </div>
      )}
    </div>
  );
}

function PasswordCard() {
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const change = useSubmit(async () => {
    await api.post("/auth/change-password", { current_password: current, new_password: next });
    setCurrent("");
    setNext("");
    toast.info(t.saved);
  }, toast.error);
  return (
    <form
      className="card"
      onSubmit={(event) => {
        event.preventDefault();
        void change.run();
      }}
    >
      <h2>{t.changePassword}</h2>
      <Field label={t.currentPassword} type="password" value={current} onChange={setCurrent} autoComplete="current-password" required />
      <Field label={t.newPassword} type="password" value={next} onChange={setNext} autoComplete="new-password" required />
      <button type="submit" disabled={change.busy}>
        {t.save}
      </button>
    </form>
  );
}

export function Security() {
  const [status, reload] = useApi(() => api.get<MfaStatus>("/auth/mfa"));
  return (
    <Screen title={t.securityTitle}>
      <Async value={status} reload={reload}>
        {(s) => <MfaCard status={s} onChange={reload} />}
      </Async>
      <PasswordCard />
    </Screen>
  );
}
