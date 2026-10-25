import { useState } from "react";
import { api } from "../api/client";
import type { Approval, ApprovalDetail, ApprovalOutcome, ToolKill, WriteTool } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Async, Chip, Empty, ReasonDialog, Screen } from "../components/common";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { formatIst, formatPaise } from "../format";
import { t, type Key } from "../i18n";

type Tab = "pending" | "history";
type Pending =
  | { kind: "reject" | "reconcile"; id: string }
  | { kind: "kill" | "restore"; tool: WriteTool };

const toolName = (tool: string) => t[`tool_${tool}` as Key] ?? tool;

function Timeline({ id }: { id: string }) {
  const [detail, reload] = useApi(() => api.get<ApprovalDetail>(`/approvals/${id}`), [id]);
  return (
    <Async value={detail} reload={reload}>
      {(d) => (
        <ul className="timeline" aria-label={t.timeline}>
          {d.timeline.map((item, index) => (
            <li key={index}>
              {formatIst(item.at)} · {item.action}
              {item.actor_role ? ` (${item.actor_role})` : ""}
              {item.reason ? ` — ${item.reason}` : ""}
            </li>
          ))}
        </ul>
      )}
    </Async>
  );
}

function KillPanel({ owner }: { owner: boolean }) {
  const toast = useToast();
  const [kills, reload] = useApi(() => api.get<ToolKill[]>("/tools/kill"));
  const [pending, setPending] = useState<Pending | null>(null);
  const confirm = async (reason: string) => {
    if (!pending || (pending.kind !== "kill" && pending.kind !== "restore")) return;
    try {
      await api.post(`/tools/${pending.tool}/${pending.kind}`, { reason });
      reload();
    } catch (err) {
      toast.error(err);
    }
    setPending(null);
  };
  return (
    <section aria-label={t.killTitle}>
      <h2>{t.killTitle}</h2>
      <Async value={kills} reload={reload}>
        {(rows) => (
          <ul className="kill-list">
            {rows.map((row) => (
              <li key={row.tool}>
                <span>{toolName(row.tool)}</span>{" "}
                <Chip tone={row.killed ? "urgent" : "ok"}>{row.killed ? t.killOn : t.killOff}</Chip>
                {row.reason && <small> {row.reason}</small>}
                {owner && (
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => setPending({ kind: row.killed ? "restore" : "kill", tool: row.tool })}
                  >
                    {row.killed ? t.restoreButton : t.killButton} · {toolName(row.tool)}
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Async>
      {pending && (pending.kind === "kill" || pending.kind === "restore") && (
        <ReasonDialog
          title={`${pending.kind === "kill" ? t.killButton : t.restoreButton}: ${toolName(pending.tool)}`}
          onConfirm={(reason) => void confirm(reason)}
          onCancel={() => setPending(null)}
        />
      )}
    </section>
  );
}

export function Approvals() {
  const toast = useToast();
  const { user } = useAuth();
  const owner = user?.role === "owner";
  const [tab, setTab] = useState<Tab>("pending");
  const [open, setOpen] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [rows, reload] = useApi(
    () => api.get<Approval[]>(tab === "pending" ? "/approvals?status=pending_approval" : "/approvals?limit=200"),
    [tab],
  );

  const act = async (path: string, body?: unknown) => {
    try {
      await api.post<ApprovalOutcome>(path, body);
      if (path.endsWith("/approve")) toast.info(t.approved);
      else if (path.endsWith("/reject")) toast.info(t.rejected);
    } catch (err) {
      toast.error(err);
    }
    reload();
  };

  const confirm = async (reason: string) => {
    if (!pending || (pending.kind !== "reject" && pending.kind !== "reconcile")) return;
    const { kind, id } = pending;
    setPending(null);
    await act(`/approvals/${id}/${kind}`, { reason });
  };

  return (
    <Screen title={t.approvalsTitle}>
      <div className="row" role="tablist">
        <button type="button" role="tab" aria-selected={tab === "pending"} className={tab === "pending" ? "" : "secondary"} onClick={() => setTab("pending")}>
          {t.approvalsPending}
        </button>
        <button type="button" role="tab" aria-selected={tab === "history"} className={tab === "history" ? "" : "secondary"} onClick={() => setTab("history")}>
          {t.approvalsHistory}
        </button>
      </div>
      <Async value={rows} reload={reload}>
        {(list) =>
          list.length === 0 ? (
            <Empty>{t.approvalsEmpty}</Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t.toolLabel}</th>
                    <th>{t.orderLabel}</th>
                    <th>{t.amountLabel}</th>
                    <th>{t.statusLabel}</th>
                    <th>{t.ageLabel}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {list.map((a) => (
                    <tr key={a.id}>
                      <td>{toolName(a.tool)}</td>
                      <td>{a.order_ref ?? "—"}</td>
                      <td>{formatPaise(a.amount_paise)}</td>
                      <td>
                        <Chip tone={a.status === "failed" || a.status === "needs_review" ? "warn" : undefined}>{a.status}</Chip>
                        {a.overdue && <Chip tone="urgent">{t.overdue}</Chip>}
                      </td>
                      <td>{formatIst(a.created_at)}</td>
                      <td>
                        <div className="row">
                          <button type="button" className="secondary" onClick={() => setOpen(open === a.id ? null : a.id)}>
                            {t.timeline}
                          </button>
                          {owner && a.status === "pending_approval" && (
                            <>
                              <button type="button" onClick={() => void act(`/approvals/${a.id}/approve`)}>
                                {t.approve}
                              </button>
                              <button type="button" className="secondary" onClick={() => setPending({ kind: "reject", id: a.id })}>
                                {t.reject}
                              </button>
                            </>
                          )}
                          {owner && a.status === "needs_review" && (
                            <button type="button" onClick={() => setPending({ kind: "reconcile", id: a.id })}>
                              {t.reconcile}
                            </button>
                          )}
                        </div>
                        {open === a.id && <Timeline id={a.id} />}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Async>
      <KillPanel owner={owner} />
      {pending && (pending.kind === "reject" || pending.kind === "reconcile") && (
        <ReasonDialog
          title={pending.kind === "reject" ? t.reject : t.reconcile}
          onConfirm={(reason) => void confirm(reason)}
          onCancel={() => setPending(null)}
        />
      )}
    </Screen>
  );
}
