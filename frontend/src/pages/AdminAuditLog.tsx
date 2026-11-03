import { useState } from "react";
import { api, downloadFile } from "../api/client";
import type { AuditLogRow, Page } from "../api/types";
import { Async, Chip, Empty, Screen } from "../components/common";
import { Icon } from "../components/Icons";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { formatIst } from "../format";
import { t } from "../i18n";

type RangeKey = "all" | "today" | "7d" | "30d";

const ACTION_TONES: ("blue" | "green" | "amber" | "red" | "purple" | "gray")[] = ["blue", "green", "amber", "purple", "gray"];

function actionTone(action: string): string {
  const key = action.split(".")[0] ?? action;
  let hash = 0;
  for (let i = 0; i < key.length; i++) hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
  return ACTION_TONES[hash % ACTION_TONES.length];
}

function rangeToSinceUntil(range: RangeKey): { since?: string; until?: string } {
  if (range === "all") return {};
  const now = new Date();
  const since = new Date(now);
  if (range === "today") since.setHours(0, 0, 0, 0);
  else if (range === "7d") since.setDate(since.getDate() - 7);
  else since.setDate(since.getDate() - 30);
  return { since: since.toISOString(), until: now.toISOString() };
}

export function AdminAuditLog() {
  const toast = useToast();
  const [action, setAction] = useState("");
  const [entityType, setEntityType] = useState("");
  const [range, setRange] = useState<RangeKey>("all");
  const [applied, setApplied] = useState({ action: "", entityType: "", range: "all" as RangeKey });
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null]);
  const [cursorIndex, setCursorIndex] = useState(0);
  const [viewing, setViewing] = useState<AuditLogRow | null>(null);

  const cursor = cursorStack[cursorIndex] ?? null;
  const { since, until } = rangeToSinceUntil(applied.range);

  const [list, reload] = useApi(
    () =>
      api.get<Page<AuditLogRow>>(
        `/admin/audit-log?${new URLSearchParams({
          ...(applied.action ? { action: applied.action } : {}),
          ...(applied.entityType ? { entity_type: applied.entityType } : {}),
          ...(since ? { since } : {}),
          ...(until ? { until } : {}),
          ...(cursor ? { cursor } : {}),
          limit: "25",
        }).toString()}`,
      ),
    [applied, cursor],
  );

  const applyFilters = () => {
    setApplied({ action: action.trim(), entityType: entityType.trim(), range });
    setCursorStack([null]);
    setCursorIndex(0);
  };

  const exportCsv = async () => {
    const query = new URLSearchParams({
      ...(applied.action ? { action: applied.action } : {}),
      ...(applied.entityType ? { entity_type: applied.entityType } : {}),
      ...(since ? { since } : {}),
      ...(until ? { until } : {}),
    }).toString();
    try {
      await downloadFile(`/admin/audit-log/export.csv${query ? `?${query}` : ""}`, "audit-log.csv");
    } catch (err) {
      toast.error(err);
    }
  };

  return (
    <Screen title={t.auditLogTitle}>
      <section className="card pad">
        <form
          className="row"
          onSubmit={(event) => {
            event.preventDefault();
            applyFilters();
          }}
        >
          <input aria-label={t.filterAction} placeholder={t.filterAction} value={action} onChange={(event) => setAction(event.target.value)} />
          <input aria-label={t.filterEntityType} placeholder={t.filterEntityType} value={entityType} onChange={(event) => setEntityType(event.target.value)} />
          <label className="field" style={{ marginBottom: 0 }}>
            <span className="sr-only">{t.dateRangeLabel}</span>
            <select aria-label={t.dateRangeLabel} value={range} onChange={(event) => setRange(event.target.value as RangeKey)}>
              <option value="all">{t.dateRangeAll}</option>
              <option value="today">{t.dateRangeToday}</option>
              <option value="7d">{t.dateRange7d}</option>
              <option value="30d">{t.dateRange30d}</option>
            </select>
          </label>
          <button type="submit" className="btn">
            <Icon name="filter" className="sm" /> {t.searchBrands}
          </button>
          <button type="button" className="btn outline-blue" onClick={() => void exportCsv()}>
            <Icon name="download" className="sm" /> {t.exportCsv}
          </button>
        </form>
      </section>
      <Async value={list} reload={reload}>
        {(page) =>
          page.items.length === 0 ? (
            <Empty>{t.noRows}</Empty>
          ) : (
            <div className="card" style={{ padding: 0 }}>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Action</th>
                      <th>{t.entityType}</th>
                      <th>{t.actorRole}</th>
                      <th>{t.reasonColumn}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {page.items.map((r) => (
                      <tr key={r.id}>
                        <td>{formatIst(r.occurred_at)}</td>
                        <td>
                          <span className={`pill ${actionTone(r.action)}`}>{r.action}</span>
                        </td>
                        <td>{r.entity_type ?? "-"}</td>
                        <td>{r.actor_role ?? "-"}</td>
                        <td>{r.reason ?? "-"}</td>
                        <td>
                          <button type="button" className="secondary" onClick={() => setViewing(r)}>
                            {t.viewDetails}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="pagination">
                <span>{page.items.length} rows</span>
                <div>
                  <button type="button" disabled={cursorIndex === 0} onClick={() => setCursorIndex((i) => Math.max(0, i - 1))}>
                    {t.prevPage}
                  </button>
                  <button
                    type="button"
                    disabled={!page.next_cursor}
                    onClick={() => {
                      if (!page.next_cursor) return;
                      const next = cursorStack.slice(0, cursorIndex + 1);
                      next.push(page.next_cursor);
                      setCursorStack(next);
                      setCursorIndex(next.length - 1);
                    }}
                  >
                    {t.nextPage}
                  </button>
                </div>
              </div>
            </div>
          )
        }
      </Async>
      {viewing && (
        <div className="dialog" role="dialog" aria-modal="true" aria-label={t.detailsTitle}>
          <div className="card" style={{ maxWidth: 480 }}>
            <div className="card-head">
              <h3 className="card-title">{t.detailsTitle}</h3>
            </div>
            <div className="detail-line">
              <span>Time</span>
              <b>{formatIst(viewing.occurred_at)}</b>
            </div>
            <div className="detail-line">
              <span>Action</span>
              <b>{viewing.action}</b>
            </div>
            <div className="detail-line">
              <span>{t.entityType}</span>
              <b>{viewing.entity_type ?? "-"}</b>
            </div>
            <div className="detail-line">
              <span>Entity id</span>
              <b>{viewing.entity_id ?? "-"}</b>
            </div>
            <div className="detail-line">
              <span>{t.actorRole}</span>
              <b>{viewing.actor_role ?? "-"}</b>
            </div>
            <div className="detail-line">
              <span>{t.reasonColumn}</span>
              <b>{viewing.reason ?? "-"}</b>
            </div>
            <div className="row" style={{ marginTop: 12 }}>
              <button type="button" className="secondary" onClick={() => setViewing(null)}>
                {t.closeDialog}
              </button>
            </div>
          </div>
        </div>
      )}
    </Screen>
  );
}
