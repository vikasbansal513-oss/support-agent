import { useState } from "react";
import { api } from "../api/client";
import type { Alert, Page } from "../api/types";
import { Async, Chip, Empty, ReasonDialog, Screen } from "../components/common";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { formatIst } from "../format";
import { t } from "../i18n";

const TONE: Record<Alert["severity"], "ok" | "warn" | "urgent"> = {
  info: "ok",
  warning: "warn",
  critical: "urgent",
};

export function AdminAlerts() {
  const toast = useToast();
  const [status, setStatus] = useState<"open" | "resolved">("open");
  const [list, reload] = useApi(() => api.get<Page<Alert>>(`/admin/alerts?status=${status}`), [status]);
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

  return (
    <Screen title={t.alertsTitle}>
      <div className="row">
        <button type="button" className={status === "open" ? undefined : "secondary"} onClick={() => setStatus("open")}>
          {t.showOpen}
        </button>
        <button type="button" className={status === "resolved" ? undefined : "secondary"} onClick={() => setStatus("resolved")}>
          {t.showResolved}
        </button>
      </div>
      <Async value={list} reload={reload}>
        {(page) =>
          page.items.length === 0 ? (
            <Empty>{t.noOpenAlerts}</Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Severity</th>
                    <th>Kind</th>
                    <th>Message</th>
                    <th>Time</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((a) => (
                    <tr key={a.id}>
                      <td><Chip tone={TONE[a.severity]}>{a.severity}</Chip></td>
                      <td>{a.kind}</td>
                      <td>{a.message}</td>
                      <td>{formatIst(a.created_at)}</td>
                      <td>
                        {a.status === "open" && (
                          <button type="button" className="secondary" onClick={() => setResolving(a)}>
                            {t.resolve}
                          </button>
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
      {resolving && (
        <ReasonDialog title={`${t.resolve}: ${resolving.kind}`} onConfirm={(reason) => void confirm(reason)} onCancel={() => setResolving(null)} />
      )}
    </Screen>
  );
}
