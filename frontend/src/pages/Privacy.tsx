import { useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { PrivacyRequest, PublicNotice } from "../api/types";
import { Async, Chip, Empty, Field, ReasonDialog, Screen, useSubmit } from "../components/common";
import { Icon } from "../components/Icons";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { formatIst } from "../format";
import { t, type Key } from "../i18n";

type Kind = PrivacyRequest["kind"];
type CorrectionField = NonNullable<PrivacyRequest["correction_field"]>;
const KINDS: Kind[] = ["export", "delete", "correct"];
const CORRECTION_FIELDS: CorrectionField[] = ["phone", "email"];

export function PrivacyRequests() {
  const toast = useToast();
  const [list, reload] = useApi(() => api.get<PrivacyRequest[]>("/privacy/requests"));
  const [subject, setSubject] = useState("");
  const [kind, setKind] = useState<Kind>("export");
  const [correctionField, setCorrectionField] = useState<CorrectionField>("phone");
  const [correctionValue, setCorrectionValue] = useState("");
  const [rejecting, setRejecting] = useState<string | null>(null);
  const create = useSubmit(
    async () => {
      await api.post("/privacy/requests", {
        subject_ref: subject.trim(),
        kind,
        ...(kind === "correct"
          ? { correction_field: correctionField, correction_value: correctionValue.trim() }
          : {}),
      });
      toast.info(t.privacyCreated);
      setSubject("");
      setCorrectionValue("");
      reload();
    },
    (err) => toast.error(err),
  );
  const [approving, setApproving] = useState<string | null>(null);
  const approve = async (id: string) => {
    setApproving(id);
    try {
      await api.post(`/privacy/requests/${id}/approve`);
      toast.info(t.privacyApproved);
      reload();
    } catch (err) {
      toast.error(err);
    } finally {
      setApproving(null);
    }
  };
  const reject = async (id: string, reason: string) => {
    setRejecting(null);
    try {
      await api.post(`/privacy/requests/${id}/reject`, { reason });
      toast.info(t.privacyRejected);
      reload();
    } catch (err) {
      toast.error(err);
    }
  };
  const download = useSubmit(
    async () => {
      const data = await api.get<{ conversations: unknown[] }>(`/privacy/export?subject_ref=${encodeURIComponent(subject.trim())}`);
      if (data.conversations.length === 0) {
        toast.info(t.privacyNothing);
        return;
      }
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = "customer-export.json";
      link.rel = "noopener";
      document.body.appendChild(link);
      link.click();
      link.remove();
      // Delay the revoke past click() returning - some browsers hand the
      // download off asynchronously and revoking too early cuts it short.
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      reload();
    },
    (err) => toast.error(err),
  );
  const correctionReady = kind !== "correct" || correctionValue.trim().length > 0;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (subject.trim().length >= 3 && correctionReady) void create.run();
  };
  return (
    <Screen title={t.privacyTitle}>
      <form onSubmit={submit} className="card privacy-form-card">
        <div className="privacy-heading">
          <span className="soft-icon">
            <Icon name="shield" />
          </span>
          <div>
            <p>{t.privacyIntro}</p>
          </div>
        </div>
        <div className="privacy-fields">
          <Field label={t.privacySubject} value={subject} onChange={setSubject} required />
          <label className="field">
            <span className="field-label">{t.privacyKind}</span>
            <select value={kind} onChange={(event) => setKind(event.target.value as Kind)}>
              {KINDS.map((k) => (
                <option key={k} value={k}>
                  {t[`privacyKind_${k}` as Key]}
                </option>
              ))}
            </select>
          </label>
          {kind === "correct" && (
            <>
              <label className="field">
                <span className="field-label">{t.privacyCorrectionField}</span>
                <select
                  value={correctionField}
                  onChange={(event) => setCorrectionField(event.target.value as CorrectionField)}
                >
                  {CORRECTION_FIELDS.map((f) => (
                    <option key={f} value={f}>
                      {t[`privacyCorrectionField_${f}` as Key]}
                    </option>
                  ))}
                </select>
              </label>
              <Field
                label={t.privacyCorrectionValue}
                value={correctionValue}
                onChange={setCorrectionValue}
                required
              />
            </>
          )}
          <div className="row head-actions">
            <button type="submit" disabled={create.busy || subject.trim().length < 3 || !correctionReady}>
              {t.privacyCreate}
            </button>
            <button type="button" className="secondary" disabled={download.busy || subject.trim().length < 3} onClick={() => void download.run()}>
              <Icon name="download" className="sm" />
              {t.privacyDownload}
            </button>
          </div>
        </div>
      </form>
      <Async value={list} reload={reload}>
        {(rows) =>
          rows.length === 0 ? (
            <Empty>{t.privacyEmpty}</Empty>
          ) : (
            <div className="card pad table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>{t.privacyKind}</th>
                    <th>Status</th>
                    <th>{t.privacyDue}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id}>
                      <td>
                        {t[`privacyKind_${r.kind}` as Key]}
                        {r.kind === "correct" && r.correction_field && (
                          <div className="card-sub">
                            {t[`privacyCorrectionField_${r.correction_field}` as Key]}: {r.correction_value}
                          </div>
                        )}
                      </td>
                      <td>
                        <Chip tone={r.status === "done" ? "ok" : r.status === "open" ? "warn" : undefined}>{r.status}</Chip>
                      </td>
                      <td>{formatIst(r.due_at)}</td>
                      <td>
                        {r.kind === "correct" && r.status === "open" && (
                          <div className="row">
                            <button type="button" className="btn primary small" disabled={approving === r.id} onClick={() => void approve(r.id)}>
                              {t.privacyApprove}
                            </button>
                            <button type="button" className="btn danger small" onClick={() => setRejecting(r.id)}>
                              {t.reject}
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Async>
      {rejecting && (
        <ReasonDialog
          title={t.reject}
          onConfirm={(reason) => void reject(rejecting, reason)}
          onCancel={() => setRejecting(null)}
        />
      )}
    </Screen>
  );
}

export function NoticePage({ kind }: { kind: "privacy" | "terms" }) {
  const [notice, reload] = useApi(() => api.get<PublicNotice>(kind === "privacy" ? "/public/privacy-notice" : "/public/terms"), [kind]);
  return (
    <main className="notice">
      <Async value={notice} reload={reload}>
        {(n) => (
          <>
            {n.draft && (
              <p className="banner" role="status">
                {t.noticeDraft}
              </p>
            )}
            <article dangerouslySetInnerHTML={{ __html: n.html }} />
            {n.grievance_email && (
              <p>
                {t.grievance}: {n.grievance_email}
              </p>
            )}
          </>
        )}
      </Async>
    </main>
  );
}
