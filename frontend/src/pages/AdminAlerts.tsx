import { useState } from "react";
import { api } from "../api/client";
import type { Alert, Page } from "../api/types";
import { Async, Chip, Empty, ReasonDialog, Screen, StatCard } from "../components/common";
import { Icon } from "../components/Icons";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { formatIst } from "../format";
import { t } from "../i18n";

const TONE: Record<Alert["severity"], "ok" | "warn" | "urgent"> = {
  info: "ok",
  warning: "warn",
  high: "urgent",
  critical: "urgent",
};

const PAGE_SIZE = 8;

export function AdminAlerts() {
  const toast = useToast();
  const [status, setStatus] = useState<"open" | "acknowledged" | "resolved">("open");
  const [severity, setSeverity] = useState<"" | Alert["severity"]>("");
  const [kind, setKind] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [list, reload] = useApi(
    () => api.get<Page<Alert>>(`/admin/alerts?status=${status}${severity ? `&severity=${severity}` : ""}`),
    [status, severity],
  );
  const [openCount] = useApi(() => api.get<Page<Alert>>("/admin/alerts?status=open"));
  const [resolvedCount] = useApi(() => api.get<Page<Alert>>("/admin/alerts?status=resolved"));
  const [resolving, setResolving] = useState<Alert | null>(null);

  const confirm = async (reason: string) => {
    if (!resolving) return;
    try {
      await api.post(`/admin/alerts/${resolving.id}/resolve`, { reason });
      setResolving(null);
      reload();
    } catch (err) {
      toast.error(err);
      setResolving(null);
    }
  };

  const acknowledge = async (a: Alert) => {
    try {
      await api.post(`/admin/alerts/${a.id}/acknowledge`, {});
      reload();
    } catch (err) {
      toast.error(err);
    }
  };

  const resetFilters = () => {
    setSeverity("");
    setKind("");
    setSearch("");
    setPage(1);
  };

  const openItems = openCount.state === "data" ? openCount.data.items : [];
  const criticalN = openItems.filter((a) => a.severity === "critical" || a.severity === "high").length;
  const warningN = openItems.filter((a) => a.severity === "warning").length;
  const infoN = openItems.filter((a) => a.severity === "info").length;
  const resolvedN = resolvedCount.state === "data" ? resolvedCount.data.items.length : 0;

  return (
    <Screen title={t.alertsTitle}>
      <div className="stats">
        <StatCard label={t.severityCritical} value={criticalN} tone={criticalN > 0 ? "warn" : undefined} icon="info" />
        <StatCard label={t.severityWarning} value={warningN} tone={warningN > 0 ? "warn" : undefined} icon="bell" />
        <StatCard label={t.severityInfo} value={infoN} icon="info" />
        <StatCard label={t.statusResolvedLabel} value={resolvedN} tone="ok" icon="check-circle" />
      </div>

      <section className="card pad">
        <div className="row">
          <button type="button" className={status === "open" ? undefined : "secondary"} onClick={() => { setStatus("open"); setPage(1); }}>
            {t.showOpen}
          </button>
          <button type="button" className={status === "acknowledged" ? undefined : "secondary"} onClick={() => { setStatus("acknowledged"); setPage(1); }}>
            Acknowledged
          </button>
          <button type="button" className={status === "resolved" ? undefined : "secondary"} onClick={() => { setStatus("resolved"); setPage(1); }}>
            {t.showResolved}
          </button>
        </div>
        <div className="filters">
          <input className="search" placeholder={t.searchAlertsPlaceholder} value={search} onChange={(event) => { setSearch(event.target.value); setPage(1); }} />
          <select
            aria-label={t.severityLabel}
            value={severity}
            onChange={(event) => { setSeverity(event.target.value as typeof severity); setPage(1); }}
          >
            <option value="">{t.allSeverities}</option>
            <option value="critical">critical</option>
            <option value="high">high</option>
            <option value="warning">warning</option>
            <option value="info">info</option>
          </select>
          <input aria-label={t.kindLabel} placeholder={t.allKinds} value={kind} onChange={(event) => { setKind(event.target.value); setPage(1); }} style={{ width: 160 }} />
          <div className="filter-right">
            <button type="button" className="btn small" onClick={resetFilters}>
              <Icon name="refresh" className="sm" /> {t.resetFilters}
            </button>
          </div>
        </div>
      </section>

      <Async value={list} reload={reload}>
        {(p) => {
          const filtered = p.items.filter((a) => {
            if (kind.trim() && !a.kind.toLowerCase().includes(kind.trim().toLowerCase())) return false;
            if (!search.trim()) return true;
            return [a.message, a.tenant_name, a.kind, a.service].filter(Boolean).join(" ").toLowerCase().includes(search.trim().toLowerCase());
          });
          const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
          const pageItems = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

          return filtered.length === 0 ? (
            <Empty>{t.noOpenAlerts}</Empty>
          ) : (
            <div className="card ct-card" style={{ padding: 0 }}>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Severity</th>
                      <th>Kind</th>
                      <th>Client</th>
                      <th>Service</th>
                      <th>Message</th>
                      <th>Time</th>
                      <th>Status</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {pageItems.map((a) => (
                      <tr key={a.id}>
                        <td><Chip tone={TONE[a.severity]}>{a.severity}</Chip></td>
                        <td>{a.kind}</td>
                        <td>{a.tenant_name ?? "-"}</td>
                        <td>{a.service || "-"}</td>
                        <td style={{ whiteSpace: "normal", maxWidth: 420 }}>
                          {a.message}
                          {a.error_id && <span className="card-sub"> {"·"} {a.error_id}</span>}
                        </td>
                        <td>{formatIst(a.created_at)}</td>
                        <td>{a.status}</td>
                        <td>
                          <div className="row" style={{ marginBottom: 0 }}>
                            {a.status === "open" && (
                              <button type="button" className="secondary" onClick={() => void acknowledge(a)}>
                                {t.acknowledge}
                              </button>
                            )}
                            {a.status !== "resolved" && (
                              <button type="button" className="secondary" onClick={() => setResolving(a)}>
                                {t.resolve}
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="pagination">
                <span>{filtered.length} alerts</span>
                <div>
                  <button type="button" disabled={page <= 1} onClick={() => setPage((n) => Math.max(1, n - 1))}>
                    {t.prevPage}
                  </button>
                  {Array.from({ length: totalPages }, (_, i) => i + 1).map((n) => (
                    <button key={n} type="button" className={n === page ? "current" : undefined} onClick={() => setPage(n)}>
                      {n}
                    </button>
                  ))}
                  <button type="button" disabled={page >= totalPages} onClick={() => setPage((n) => Math.min(totalPages, n + 1))}>
                    {t.nextPage}
                  </button>
                </div>
              </div>
            </div>
          );
        }}
      </Async>
      {resolving && (
        <ReasonDialog title={`${t.resolve}: ${resolving.kind}`} onConfirm={(reason) => void confirm(reason)} onCancel={() => setResolving(null)} />
      )}
    </Screen>
  );
}
