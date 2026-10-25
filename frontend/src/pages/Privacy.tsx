import { useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { PrivacyRequest, PublicNotice } from "../api/types";
import { Async, Chip, Empty, Field, Screen, useSubmit } from "../components/common";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { formatIst } from "../format";
import { t, type Key } from "../i18n";

type Kind = PrivacyRequest["kind"];
const KINDS: Kind[] = ["export", "delete", "correct"];

export function PrivacyRequests() {
  const toast = useToast();
  const [list, reload] = useApi(() => api.get<PrivacyRequest[]>("/privacy/requests"));
  const [subject, setSubject] = useState("");
  const [kind, setKind] = useState<Kind>("export");
  const create = useSubmit(
    async () => {
      await api.post("/privacy/requests", { subject_ref: subject.trim(), kind });
      toast.info(t.privacyCreated);
      setSubject("");
      reload();
    },
    (err) => toast.error(err),
  );
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
      link.click();
      URL.revokeObjectURL(url);
      reload();
    },
    (err) => toast.error(err),
  );
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (subject.trim().length >= 3) void create.run();
  };
  return (
    <Screen title={t.privacyTitle}>
      <form onSubmit={submit} className="card">
        <Field label={t.privacySubject} value={subject} onChange={setSubject} required />
        <label className="field">
          <span>{t.privacyKind}</span>
          <select value={kind} onChange={(event) => setKind(event.target.value as Kind)}>
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {t[`privacyKind_${k}` as Key]}
              </option>
            ))}
          </select>
        </label>
        <div className="row">
          <button type="submit" disabled={create.busy || subject.trim().length < 3}>
            {t.privacyCreate}
          </button>
          <button type="button" className="secondary" disabled={download.busy || subject.trim().length < 3} onClick={() => void download.run()}>
            {t.privacyDownload}
          </button>
        </div>
      </form>
      <Async value={list} reload={reload}>
        {(rows) =>
          rows.length === 0 ? (
            <Empty>{t.privacyEmpty}</Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t.privacyKind}</th>
                    <th>Status</th>
                    <th>{t.privacyDue}</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id}>
                      <td>{t[`privacyKind_${r.kind}` as Key]}</td>
                      <td>
                        <Chip tone={r.status === "done" ? "ok" : r.status === "open" ? "warn" : undefined}>{r.status}</Chip>
                      </td>
                      <td>{formatIst(r.due_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Async>
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
