import { useState } from "react";
import { api } from "../api/client";
import type { Action, AgentStatus, DashboardStats, Page } from "../api/types";
import { Async, Chip, Empty, Screen, useSubmit } from "../components/common";
import { Icon } from "../components/Icons";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { t } from "../i18n";

function qualityPill(verdict: Action["verdict"]) {
  if (verdict === "correct") return <span className="pill green">Good</span>;
  if (verdict === "wrong") return <span className="pill amber">Check</span>;
  return <span className="pill gray">Pending</span>;
}

function truncate(text: string | null, max = 48) {
  if (!text) return "—";
  return text.length > max ? text.slice(0, max) + "…" : text;
}

function ReviewModal({
  action,
  draftMode,
  onDone,
  onClose,
}: {
  action: Action;
  draftMode: boolean;
  onDone: () => void;
  onClose: () => void;
}) {
  const toast = useToast();
  const [note, setNote] = useState("");
  const verdict = (value: "correct" | "wrong") =>
    useSubmit(async () => {
      await api.post<Action>(`/actions/${action.id}/verdict`, { verdict: value, note: note.trim() || null });
      onDone();
      onClose();
    }, toast.error);
  const correct = verdict("correct");
  const wrong = verdict("wrong");
  const approve = useSubmit(async () => {
    await api.post<Action>(`/actions/${action.id}/approve`);
    onDone();
    onClose();
  }, (err) => { toast.error(err); onDone(); onClose(); });
  const reject = useSubmit(async () => {
    await api.post<Action>(`/actions/${action.id}/reject`);
    onDone();
    onClose();
  }, toast.error);

  return (
    <div
      style={{
        position: "fixed", inset: 0, zIndex: 200,
        background: "rgba(16,24,40,.35)",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}
      onClick={onClose}
    >
      <div
        className="card pad"
        style={{ maxWidth: 520, width: "100%", margin: "0 16px" }}
        onClick={(e) => e.stopPropagation()}
      >
        <p><strong>{t.customerSaid}</strong></p>
        <div className="bubble bubble-in">{action.customer_message ?? "—"}</div>
        <p><strong>{t.agentDraft}</strong> <Chip>{action.status}</Chip></p>
        <div className="bubble bubble-out unsent">{action.body_text}</div>
        <label className="field" style={{ marginTop: 12 }}>
          <span className="field-label">{t.note}</span>
          <input
            className="input"
            value={note}
            maxLength={500}
            onChange={(e) => setNote(e.target.value)}
          />
        </label>
        <div className="row" style={{ gap: 8, marginTop: 14 }}>
          <button type="button" disabled={correct.busy} onClick={() => void correct.run()}>
            {t.verdictCorrect}
          </button>
          <button type="button" className="secondary" disabled={wrong.busy} onClick={() => void wrong.run()}>
            {t.verdictWrong}
          </button>
          {draftMode && action.status === "draft" && (
            <>
              <button type="button" disabled={approve.busy} onClick={() => void approve.run()}>
                {t.approve}
              </button>
              <button type="button" className="secondary" disabled={reject.busy} onClick={() => void reject.run()}>
                {t.reject}
              </button>
            </>
          )}
          <button type="button" className="secondary" style={{ marginLeft: "auto" }} onClick={onClose}>
            {t.cancel}
          </button>
        </div>
      </div>
    </div>
  );
}

function ReviewTable({
  draftMode,
  stats,
  reload: reloadStats,
}: {
  draftMode: boolean;
  stats: DashboardStats | null;
  reload: () => void;
}) {
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Action | null>(null);
  const [list, reloadList] = useApi(() =>
    api.get<Page<Action>>(draftMode ? "/actions?status=draft" : "/actions?unreviewed=true"),
  );

  const onDone = () => { reloadList(); reloadStats(); };

  const filtered = list.state === "data"
    ? list.data.items.filter((a: Action) => {
        const q = search.toLowerCase();
        return !q
          || (a.body_text ?? "").toLowerCase().includes(q)
          || (a.customer_message ?? "").toLowerCase().includes(q)
          || a.action_type.toLowerCase().includes(q);
      })
    : [];

  // stat card values from DashboardStats
  const pending = stats?.shadow_unreviewed ?? 0;
  const reviewed = (stats?.shadow_correct ?? 0) + (stats?.shadow_wrong ?? 0);
  const total = pending + reviewed;
  const positivePct = total > 0 ? Math.round((stats?.shadow_correct ?? 0) / total * 100) : 0;
  const flagged = stats?.shadow_wrong ?? 0;

  return (
    <>
      <div className="stats">
        <div className="stat-card">
          <div className="stat-icon blue"><Icon name="i-review" /></div>
          <div>
            <div className="stat-number">{pending}</div>
            <div className="stat-label">Pending review</div>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-icon green"><Icon name="i-check-circle" /></div>
          <div>
            <div className="stat-number">{reviewed}</div>
            <div className="stat-label">Reviewed today</div>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-icon purple"><Icon name="i-users" /></div>
          <div>
            <div className="stat-number">{positivePct}%</div>
            <div className="stat-label">Positive feedback</div>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-icon orange"><Icon name="i-info" /></div>
          <div>
            <div className="stat-number">{flagged}</div>
            <div className="stat-label">Flagged responses</div>
          </div>
        </div>
      </div>

      <div className="card pad">
        <div className="card-head">
          <h2 className="card-title">Recent responses for review</h2>
          <input
            className="search"
            placeholder="Search reviews..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <Async value={list} reload={reloadList}>
          {() =>
            filtered.length === 0 ? (
              <Empty>{t.emptyReview}</Empty>
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Customer</th>
                      <th>Conversation</th>
                      <th>AI Response</th>
                      <th>Quality</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map((a: Action) => (
                      <tr key={a.id}>
                        <td><b>Customer</b></td>
                        <td>{a.action_type.replace("_", " ")}</td>
                        <td>{truncate(a.body_text)}</td>
                        <td>{qualityPill(a.verdict)}</td>
                        <td>
                          <button
                            type="button"
                            className="btn small"
                            onClick={() => setSelected(a)}
                          >
                            Review
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          }
        </Async>
      </div>

      {selected && (
        <ReviewModal
          action={selected}
          draftMode={draftMode}
          onDone={onDone}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  );
}

export function Review() {
  const [status, reloadStatus] = useApi(() => api.get<AgentStatus>("/agent/status"));
  const [stats, reloadStats] = useApi(() => api.get<DashboardStats>("/dashboard/stats"));

  return (
    <Screen
      title={t.navReview}
      subtitle="Review AI responses and monitor conversations that need attention."
      actions={
        <button type="button" className="btn">Export reviews</button>
      }
    >
      <Async value={status} reload={reloadStatus}>
        {(s) => (
          <Async value={stats} reload={reloadStats}>
            {(st) => (
              <ReviewTable
                draftMode={s.mode === "draft"}
                stats={st}
                reload={reloadStats}
              />
            )}
          </Async>
        )}
      </Async>
    </Screen>
  );
}
