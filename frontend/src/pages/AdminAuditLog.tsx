import { useState } from "react";
import { api, downloadFile } from "../api/client";
import type { AuditLogRow, Page } from "../api/types";
import { Async, Empty, Screen } from "../components/common";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { formatIst } from "../format";
import { t } from "../i18n";

export function AdminAuditLog() {
  const toast = useToast();
  const [action, setAction] = useState("");
  const [entityType, setEntityType] = useState("");
  const [applied, setApplied] = useState({ action: "", entityType: "" });
  const [list, reload] = useApi(
    () =>
      api.get<Page<AuditLogRow>>(
        `/admin/audit-log?${new URLSearchParams({
          ...(applied.action ? { action: applied.action } : {}),
          ...(applied.entityType ? { entity_type: applied.entityType } : {}),
        }).toString()}`,
      ),
    [applied],
  );

  const exportCsv = async () => {
    const query = new URLSearchParams({
      ...(applied.action ? { action: applied.action } : {}),
      ...(applied.entityType ? { entity_type: applied.entityType } : {}),
    }).toString();
    try {
      await downloadFile(`/admin/audit-log/export.csv${query ? `?${query}` : ""}`, "audit-log.csv");
    } catch (err) {
      toast.error(err);
    }
  };

  return (
    <Screen title={t.auditLogTitle}>
      <form
        className="row"
        onSubmit={(event) => {
          event.preventDefault();
          setApplied({ action: action.trim(), entityType: entityType.trim() });
        }}
      >
        <input aria-label={t.filterAction} placeholder={t.filterAction} value={action} onChange={(event) => setAction(event.target.value)} />
        <input aria-label={t.filterEntityType} placeholder={t.filterEntityType} value={entityType} onChange={(event) => setEntityType(event.target.value)} />
        <button type="submit" className="secondary">{t.searchBrands}</button>
        <button type="button" className="secondary" onClick={() => void exportCsv()}>{t.exportCsv}</button>
      </form>
      <Async value={list} reload={reload}>
        {(page) =>
          page.items.length === 0 ? (
            <Empty>{t.noRows}</Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Action</th>
                    <th>{t.entityType}</th>
                    <th>{t.actorRole}</th>
                    <th>{t.reasonColumn}</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((r) => (
                    <tr key={r.id}>
                      <td>{formatIst(r.occurred_at)}</td>
                      <td>{r.action}</td>
                      <td>{r.entity_type ?? "-"}</td>
                      <td>{r.actor_role ?? "-"}</td>
                      <td>{r.reason ?? "-"}</td>
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
