import { api } from "../api/client";
import type { ControlTower } from "../api/types";
import { Async, Chip, Empty, Screen, StatCard } from "../components/common";
import { useApi } from "../components/useApi";
import { t } from "../i18n";

export function AdminControlTower() {
  const [tower, reload] = useApi(() => api.get<ControlTower>("/admin/control-tower"));
  return (
    <Screen title={t.controlTowerTitle}>
      <Async value={tower} reload={reload}>
        {(c) => (
          <>
            <div className="row">
              <Chip tone={c.health.status === "ok" ? "ok" : "urgent"}>{t.healthDb}: {c.health.db}</Chip>
              <Chip tone={c.health.worker_seconds_since !== null && c.health.worker_seconds_since > 180 ? "urgent" : "ok"}>
                {t.healthWorker}: {c.health.worker_seconds_since ?? "-"}
              </Chip>
              <Chip tone={c.health.queue_depth > 0 ? "warn" : "ok"}>{t.healthQueueDepth}: {c.health.queue_depth}</Chip>
            </div>
            <div className="grid">
              <StatCard label={t.errorRateTitle} value={`${c.error_rate_percent}%`} tone={c.error_rate_percent > 5 ? "warn" : "ok"} />
              <StatCard label={t.skippedRunsTitle} value={c.skipped_count} />
              <StatCard label={t.p95LatencyTitle} value={c.p95_latency_ms === null ? "-" : `${c.p95_latency_ms} ms`} />
              <StatCard label={t.failedTodayTitle} value={c.failed_jobs_today} tone={c.failed_jobs_today > c.failed_jobs_avg_7d ? "warn" : "ok"} />
              <StatCard label={t.failedAvg7dTitle} value={c.failed_jobs_avg_7d.toFixed(1)} />
            </div>
            <h2>{t.topTenantsTitle}</h2>
            {c.top_tenants.length === 0 ? (
              <Empty>{t.noRows}</Empty>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>{t.brandName}</th>
                      <th>{t.handledColumn}</th>
                      <th>{t.handedOffColumn}</th>
                      <th>{t.handoffRateColumn}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {c.top_tenants.map((r) => (
                      <tr key={r.tenant_id}>
                        <td>{r.name}</td>
                        <td>{r.handled}</td>
                        <td>{r.handed_off}</td>
                        <td>
                          <Chip tone={r.handoff_rate_percent > 30 ? "warn" : "ok"}>{r.handoff_rate_percent}%</Chip>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </Async>
    </Screen>
  );
}
