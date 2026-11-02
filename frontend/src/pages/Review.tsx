import { useState } from "react";
import { api } from "../api/client";
import type { Action, AgentStatus, Page } from "../api/types";
import { Async, Chip, Empty, Screen, useSubmit } from "../components/common";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { t } from "../i18n";

function ReviewCard({ action, draftMode, onDone }: { action: Action; draftMode: boolean; onDone: () => void }) {
  const toast = useToast();
  const [note, setNote] = useState("");
  const verdict = (value: "correct" | "wrong") =>
    useSubmit(async () => {
      await api.post<Action>(`/actions/${action.id}/verdict`, { verdict: value, note: note.trim() || null });
      onDone();
    }, toast.error);
  const correct = verdict("correct");
  const wrong = verdict("wrong");
  const approve = useSubmit(async () => {
    await api.post<Action>(`/actions/${action.id}/approve`);
    onDone();
  }, (err) => {
    toast.error(err);
    onDone();
  });
  const reject = useSubmit(async () => {
    await api.post<Action>(`/actions/${action.id}/reject`);
    onDone();
  }, toast.error);
  return (
    <div className="card">
      <p>
        <strong>{t.customerSaid}</strong>
      </p>
      <div className="bubble bubble-in">{action.customer_message ?? "-"}</div>
      <p>
        <strong>{t.agentDraft}</strong> <Chip>{action.status}</Chip>
      </p>
      <div className="bubble bubble-out unsent">{action.body_text}</div>
      <label className="field">
        <span className="field-label">{t.note}</span>
        <input className="input" value={note} maxLength={500} onChange={(event) => setNote(event.target.value)} />
      </label>
      <div className="verdicts">
        <button type="button" disabled={correct.busy} onClick={() => void correct.run()}>
          {t.verdictCorrect}
        </button>
        <button type="button" className="secondary" disabled={wrong.busy} onClick={() => void wrong.run()}>
          {t.verdictWrong}
        </button>
      </div>
      {draftMode && action.status === "draft" && (
        <div className="row sticky-actions">
          <button type="button" disabled={approve.busy} onClick={() => void approve.run()}>
            {t.approve}
          </button>
          <button type="button" className="secondary" disabled={reject.busy} onClick={() => void reject.run()}>
            {t.reject}
          </button>
        </div>
      )}
    </div>
  );
}

function ReviewList({ draftMode }: { draftMode: boolean }) {
  const [list, reload] = useApi(() =>
    api.get<Page<Action>>(draftMode ? "/actions?status=draft" : "/actions?unreviewed=true"),
  );
  return (
    <Async value={list} reload={reload}>
      {(page) =>
        page.items.length === 0 ? (
          <Empty>{t.emptyReview}</Empty>
        ) : (
          <>
            {page.items.map((a) => (
              <ReviewCard key={a.id} action={a} draftMode={draftMode} onDone={reload} />
            ))}
          </>
        )
      }
    </Async>
  );
}

export function Review() {
  const [status, reload] = useApi(() => api.get<AgentStatus>("/agent/status"));
  return (
    <Screen title={t.navReview}>
      <Async value={status} reload={reload}>
        {(s) => <ReviewList draftMode={s.mode === "draft"} />}
      </Async>
    </Screen>
  );
}
