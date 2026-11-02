import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type {
  Accuracy,
  AgentTaskDetail,
  AgentTaskList,
  AgentTaskStatus,
  ControlTower,
  Correctness,
  Historical,
  HistoricalSeries,
  PagePerf,
  Threshold,
  Traffic,
} from "../api/types";
import { Async, Chip, Empty, Screen, StatCard } from "../components/common";
import { useApi } from "../components/useApi";
import { type Key, t } from "../i18n";

function fmtMs(value: number | null | undefined): string {
  return value === null || value === undefined ? "-" : `${Math.round(value)} ms`;
}

function correctnessTone(c: Correctness): "ok" | "warn" | "urgent" | undefined {
  if (c === "correct") return "ok";
  if (c === "wrong") return "urgent";
  if (c === "partial") return "warn";
  return undefined;
}

const CORRECTNESS_KEYS: Record<Correctness, Key> = {
  correct: "monitorCorrectCorrect",
  wrong: "monitorCorrectWrong",
  partial: "monitorCorrectPartial",
  unverified: "monitorCorrectUnverified",
};

const STATUS_KEYS: Record<AgentTaskStatus, Key> = {
  running: "monitorStatusRunning",
  done: "monitorStatusDone",
  failed: "monitorStatusFailed",
  skipped: "monitorStatusSkipped",
};

function correctnessLabel(c: Correctness): string {
  return t[CORRECTNESS_KEYS[c]];
}

function statusLabel(s: AgentTaskStatus): string {
  return t[STATUS_KEYS[s]];
}

/** Minimal inline SVG sparkline/line-chart — no new npm dependency. */
function Sparkline({ series }: { series: HistoricalSeries }) {
  const points = series.points;
  if (points.length === 0) return <Empty>{t.monitorNoData}</Empty>;
  const values = points.map((p) => p.value);
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 1);
  const range = max - min || 1;
  const w = 600;
  const h = 120;
  const step = points.length > 1 ? w / (points.length - 1) : 0;
  const coords = points.map((p, i) => {
    const x = i * step;
    const y = h - ((p.value - min) / range) * (h - 10) - 5;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return (
    <div className="card pad">
      <div className="card-head">
        <h3 className="card-title">{series.name}</h3>
        <span className="card-sub">{values[values.length - 1]?.toFixed(1)}</span>
      </div>
      <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} preserveAspectRatio="none" role="img" aria-label={series.name}>
        <polyline points={coords.join(" ")} fill="none" stroke="currentColor" strokeWidth={2} />
      </svg>
    </div>
  );
}

function SystemHealth({ c }: { c: ControlTower }) {
  return (
    <section className="card pad">
      <h2>{t.systemHealthTitle}</h2>
      <div className="row">
        <Chip tone={c.health.status === "ok" ? "ok" : "urgent"}>
          {t.healthDb}: {c.health.db}
        </Chip>
        <Chip tone={c.health.worker_seconds_since !== null && c.health.worker_seconds_since > 180 ? "urgent" : "ok"}>
          {t.healthWorker}: {c.health.worker_seconds_since ?? "-"}
        </Chip>
        <Chip tone={c.health.queue_depth > 0 ? "warn" : "ok"}>
          {t.healthQueueDepth}: {c.health.queue_depth}
        </Chip>
      </div>
      <div className="grid">
        <StatCard label={t.errorRateTitle} value={`${c.error_rate_percent}%`} tone={c.error_rate_percent > 5 ? "warn" : "ok"} />
        <StatCard label={t.skippedRunsTitle} value={c.skipped_count} />
        <StatCard label={t.p95LatencyTitle} value={c.p95_latency_ms === null ? "-" : `${c.p95_latency_ms} ms`} />
        <StatCard label={t.failedTodayTitle} value={c.failed_jobs_today} tone={c.failed_jobs_today > c.failed_jobs_avg_7d ? "warn" : "ok"} />
        <StatCard label={t.failedAvg7dTitle} value={c.failed_jobs_avg_7d.toFixed(1)} />
        <StatCard label={t.needsReviewTodayTitle} value={c.needs_review_jobs_today} tone={c.needs_review_jobs_today > c.needs_review_jobs_avg_7d ? "warn" : "ok"} />
        <StatCard label={t.needsReviewAvg7dTitle} value={c.needs_review_jobs_avg_7d.toFixed(1)} />
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
    </section>
  );
}

function AccuracySection() {
  const [window_, setWindow] = useState<"24h" | "7d" | "30d">("7d");
  const [data, reload] = useApi(() => api.get<Accuracy>(`/admin/monitoring/accuracy?window=${window_}`), [window_]);
  return (
    <section className="card pad">
      <div className="card-head">
        <h2>{t.monitorAccuracySection}</h2>
        <label className="field">
          <span>{t.monitorAccuracyWindow}</span>
          <select value={window_} onChange={(e) => setWindow(e.target.value as typeof window_)}>
            <option value="24h">{t.monitorAccuracy24h}</option>
            <option value="7d">{t.monitorAccuracy7d}</option>
            <option value="30d">{t.monitorAccuracy30d}</option>
          </select>
        </label>
      </div>
      <Async value={data} reload={reload}>
        {(a) => (
          <>
            <div className="grid">
              <StatCard label={t.monitorAccuracyPercent} value={`${a.overall.accuracy_percent}%`} tone={a.overall.accuracy_percent < 80 ? "warn" : "ok"} />
              <StatCard label={t.monitorTaskSuccessPercent} value={`${a.overall.task_success_percent}%`} />
              <StatCard label={t.monitorHumanCorrectionPercent} value={`${a.overall.human_correction_percent}%`} />
              <StatCard label={t.monitorHandoffPercent} value={`${a.overall.handoff_percent}%`} />
              <StatCard label={t.monitorEvaluatedCount} value={a.overall.total_evaluated} />
            </div>
            <p className="card-sub">{t.monitorAccuracyUnverifiedNote}</p>
            {a.by_tenant.length > 0 && (
              <>
                <h3>{t.monitorAccuracyByClient}</h3>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>{t.brandName}</th>
                        <th>{t.monitorAccuracyPercent}</th>
                        <th>{t.monitorTaskSuccessPercent}</th>
                        <th>{t.monitorHandoffPercent}</th>
                        <th>{t.monitorEvaluatedCount}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {a.by_tenant.map((row) => (
                        <tr key={row.tenant_id}>
                          <td>{row.tenant_name}</td>
                          <td>
                            <Chip tone={row.accuracy_percent < 80 ? "warn" : "ok"}>{row.accuracy_percent}%</Chip>
                          </td>
                          <td>{row.task_success_percent}%</td>
                          <td>{row.handoff_percent}%</td>
                          <td>{row.total_evaluated}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </>
        )}
      </Async>
    </section>
  );
}

function TaskHistorySection() {
  const [status, setStatus] = useState<AgentTaskStatus | "">("");
  const [category, setCategory] = useState("");
  const [page, setPage] = useState(1);
  const [openId, setOpenId] = useState<string | null>(null);
  const pageSize = 25;
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (status) query.set("status", status);
  if (category.trim()) query.set("category", category.trim());
  const [data, reload] = useApi(() => api.get<AgentTaskList>(`/admin/monitoring/agent-tasks?${query.toString()}`), [status, category, page]);
  const [detail, reloadDetail] = useApi(
    () => (openId ? api.get<AgentTaskDetail>(`/admin/monitoring/agent-tasks/${openId}`) : Promise.resolve(null)),
    [openId],
  );

  return (
    <section className="card pad">
      <h2>{t.monitorTaskHistorySection}</h2>
      <div className="row">
        <label className="field">
          <span>{t.monitorFilterStatus}</span>
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value as AgentTaskStatus | "");
              setPage(1);
            }}
          >
            <option value="">{t.monitorFilterAllStatuses}</option>
            {(["running", "done", "failed", "skipped"] as AgentTaskStatus[]).map((s) => (
              <option key={s} value={s}>
                {statusLabel(s)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>{t.monitorFilterCategory}</span>
          <input
            value={category}
            placeholder={t.monitorFilterAllCategories}
            onChange={(e) => {
              setCategory(e.target.value);
              setPage(1);
            }}
          />
        </label>
      </div>
      <Async value={data} reload={reload}>
        {(list) =>
          list.items.length === 0 ? (
            <Empty>{t.noRows}</Empty>
          ) : (
            <>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>{t.monitorColTime}</th>
                      <th>{t.monitorColClient}</th>
                      <th>{t.monitorColCategory}</th>
                      <th>{t.monitorColStatus}</th>
                      <th>{t.monitorColCorrectness}</th>
                      <th>{t.monitorColDuration}</th>
                      <th>{t.monitorColModel}</th>
                      <th>{t.monitorColHandedOff}</th>
                      <th>{t.monitorColAction}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {list.items.map((row) => (
                      <tr key={row.run_id}>
                        <td>{new Date(row.started_at).toLocaleString()}</td>
                        <td>{row.tenant_name}</td>
                        <td>{row.task_category}</td>
                        <td>{statusLabel(row.status)}</td>
                        <td>
                          <Chip tone={correctnessTone(row.correctness)}>{correctnessLabel(row.correctness)}</Chip>
                        </td>
                        <td>{fmtMs(row.duration_ms)}</td>
                        <td>{row.llm_model ?? "-"}</td>
                        <td>{row.handed_off ? t.monitorYes : t.monitorNo}</td>
                        <td>
                          <button type="button" className="secondary" onClick={() => setOpenId(row.run_id)}>
                            {t.monitorViewDetails}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="row">
                <button type="button" className="secondary" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                  ‹
                </button>
                <span>{page}</span>
                <button type="button" className="secondary" disabled={page * pageSize >= list.total} onClick={() => setPage((p) => p + 1)}>
                  ›
                </button>
              </div>
            </>
          )
        }
      </Async>
      {openId && (
        <div className="dialog" role="dialog" aria-modal="true" aria-label={t.monitorTaskDetailTitle}>
          <div className="card" style={{ maxWidth: 720, maxHeight: "80vh", overflow: "auto" }}>
            <div className="card-head">
              <h3 className="card-title">{t.monitorTaskDetailTitle}</h3>
              <button type="button" className="secondary" onClick={() => setOpenId(null)}>
                {t.monitorBackToTaskHistory}
              </button>
            </div>
            <Async value={detail} reload={reloadDetail}>
              {(d) =>
                d === null ? (
                  <Empty>{t.noRows}</Empty>
                ) : (
                  <>
                    <p>
                      <b>{d.tenant_name}</b> · {statusLabel(d.status)} · {new Date(d.started_at).toLocaleString()}
                    </p>
                    {d.verdict && (
                      <p>
                        {t.monitorDetailVerdict}: {d.verdict}
                      </p>
                    )}
                    {d.report_reason && (
                      <p>
                        {t.monitorDetailReportReason}: {d.report_reason}
                      </p>
                    )}
                    <h4>{t.monitorDetailActions}</h4>
                    {d.actions.length === 0 ? (
                      <Empty>{t.noRows}</Empty>
                    ) : (
                      <ul>
                        {d.actions.map((a, i) => (
                          <li key={i}>
                            {a.action_type} — {a.status}
                            {a.tool_name ? ` (${a.tool_name})` : ""}
                            {a.error ? `: ${a.error}` : ""}
                          </li>
                        ))}
                      </ul>
                    )}
                    <h4>{t.monitorDetailLlmCalls}</h4>
                    {d.llm_calls.length === 0 ? (
                      <Empty>{t.noRows}</Empty>
                    ) : (
                      <ul>
                        {d.llm_calls.map((call, i) => (
                          <li key={i}>
                            {call.purpose} · {call.provider}/{call.model} · {call.input_tokens}+{call.output_tokens} tok · {call.latency_ms} ms · {call.outcome}
                          </li>
                        ))}
                      </ul>
                    )}
                    {d.decision && (
                      <>
                        <h4>{t.monitorDetailDecision}</h4>
                        <pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>{JSON.stringify(d.decision, null, 2)}</pre>
                      </>
                    )}
                    {d.trace.length > 0 && (
                      <>
                        <h4>{t.monitorDetailTrace}</h4>
                        <pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>{JSON.stringify(d.trace, null, 2)}</pre>
                      </>
                    )}
                  </>
                )
              }
            </Async>
          </div>
        </div>
      )}
    </section>
  );
}

function PagePerfSection() {
  const [data, reload] = useApi(() => api.get<PagePerf>("/admin/monitoring/page-performance"));
  return (
    <section className="card pad">
      <h2>{t.monitorPagePerfSection}</h2>
      <Async value={data} reload={reload}>
        {(p) =>
          p.rows.length === 0 ? (
            <Empty>{t.noRows}</Empty>
          ) : (
            <>
              <div className="row">
                {p.slowest_client && (
                  <Chip tone="warn">
                    {t.monitorSlowestClient}: {p.slowest_client}
                  </Chip>
                )}
                {p.slowest_page && (
                  <Chip tone="warn">
                    {t.monitorSlowestPage}: {p.slowest_page}
                  </Chip>
                )}
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>{t.brandName}</th>
                      <th>{t.monitorColRoute}</th>
                      <th>{t.monitorColSamples}</th>
                      <th>{t.monitorColAvgLoad}</th>
                      <th>{t.monitorColP95Load}</th>
                      <th>{t.monitorColP99Load}</th>
                      <th>{t.monitorColAvgLcp}</th>
                      <th>{t.monitorColAvgTtfb}</th>
                      <th>{t.monitorColFailedApi}</th>
                      <th>{t.monitorColStatus}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {p.rows.map((row, i) => (
                      <tr key={i}>
                        <td>{row.tenant_name}</td>
                        <td>{row.route}</td>
                        <td>{row.samples}</td>
                        <td>{row.avg_load_ms === null ? "-" : fmtMs(row.avg_load_ms)}</td>
                        <td>{fmtMs(row.p95_load_ms)}</td>
                        <td>{fmtMs(row.p99_load_ms)}</td>
                        <td>{row.avg_lcp_ms === null ? "-" : fmtMs(row.avg_lcp_ms)}</td>
                        <td>{row.avg_ttfb_ms === null ? "-" : fmtMs(row.avg_ttfb_ms)}</td>
                        <td>{row.failed_api_count}</td>
                        <td>
                          <Chip tone={row.status === "slow" ? "warn" : "ok"}>{row.status === "slow" ? t.monitorStatusSlow : t.monitorStatusGood}</Chip>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )
        }
      </Async>
    </section>
  );
}

function TrafficSection() {
  const [data, reload] = useApi(() => api.get<Traffic>("/admin/monitoring/traffic"));
  return (
    <section className="card pad">
      <h2>{t.monitorTrafficSection}</h2>
      <Async value={data} reload={reload}>
        {(tr) => (
          <>
            <div className="grid">
              <StatCard label={t.monitorTotalClients} value={tr.total_clients} />
              <StatCard label={t.monitorActiveClients} value={tr.active_clients} />
              <StatCard label={t.monitorActiveUsers} value={tr.active_users_5m} />
              <StatCard label={t.monitorRunningJobs} value={tr.running_jobs} />
              <StatCard label={t.monitorQueueDepthStat} value={tr.queue_depth} tone={tr.queue_depth > 0 ? "warn" : "ok"} />
              <StatCard label={t.monitorDbConnections} value={tr.db_connections} />
              <StatCard label={t.monitorProcessMemory} value={`${tr.process_rss_mb.toFixed(0)} MB`} />
            </div>
            <h3>{t.monitorApiPerfSection}</h3>
            <div className="grid">
              <StatCard label={t.monitorCallsPerMinute} value={tr.integration_calls_per_minute.toFixed(1)} />
              <StatCard label={t.monitorAvgLatency} value={tr.avg_integration_latency_ms === null ? "-" : fmtMs(tr.avg_integration_latency_ms)} />
              <StatCard label={t.monitorP95Latency} value={tr.p95_integration_latency_ms === null ? "-" : fmtMs(tr.p95_integration_latency_ms)} />
              <StatCard label={t.monitorErrorRatePercent} value={`${tr.integration_error_rate_percent}%`} tone={tr.integration_error_rate_percent > 5 ? "warn" : "ok"} />
            </div>
            <p className="card-sub">{tr.note}</p>
            {tr.by_client.length > 0 && (
              <>
                <h3>{t.monitorByClientTitle}</h3>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>{t.brandName}</th>
                        <th>{t.monitorColAgentRuns24h}</th>
                        <th>{t.monitorColIntegrationCalls1h}</th>
                        <th>{t.monitorAvgLatency}</th>
                        <th>{t.monitorColFailedIntegrationCalls1h}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tr.by_client.map((row) => (
                        <tr key={row.tenant_id}>
                          <td>{row.name}</td>
                          <td>{row.agent_runs_24h}</td>
                          <td>{row.integration_calls_1h}</td>
                          <td>{row.avg_integration_latency_ms === null ? "-" : fmtMs(row.avg_integration_latency_ms)}</td>
                          <td>{row.failed_integration_calls_1h}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </>
        )}
      </Async>
    </section>
  );
}

function HistoricalSection() {
  const [range, setRange] = useState<"24h" | "7d" | "30d">("7d");
  const [data, reload] = useApi(() => api.get<Historical>(`/admin/monitoring/historical?range=${range}`), [range]);
  return (
    <section className="card pad">
      <div className="card-head">
        <h2>{t.monitorHistoricalSection}</h2>
        <label className="field">
          <span>{t.monitorHistoricalRange}</span>
          <select value={range} onChange={(e) => setRange(e.target.value as typeof range)}>
            <option value="24h">{t.monitorAccuracy24h}</option>
            <option value="7d">{t.monitorAccuracy7d}</option>
            <option value="30d">{t.monitorAccuracy30d}</option>
          </select>
        </label>
      </div>
      <Async value={data} reload={reload}>
        {(h) => (
          <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
            {h.series.map((s) => (
              <Sparkline key={s.name} series={s} />
            ))}
          </div>
        )}
      </Async>
    </section>
  );
}

function ThresholdsSection() {
  const [data, reload] = useApi(() => api.get<Threshold[]>("/admin/monitoring/thresholds"));
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(false);

  const save = async (rows: Threshold[]) => {
    const values: Record<string, number> = {};
    for (const row of rows) {
      const raw = edits[row.key];
      if (raw !== undefined && raw.trim() !== "" && !Number.isNaN(Number(raw))) values[row.key] = Number(raw);
    }
    if (Object.keys(values).length === 0) return;
    await api.put<Threshold[]>("/admin/monitoring/thresholds", { values });
    setEdits({});
    setSaved(true);
    reload();
  };

  return (
    <section className="card pad">
      <h2>{t.monitorThresholdsSection}</h2>
      <Async value={data} reload={reload}>
        {(rows) => (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Key</th>
                    <th>Value</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.key}>
                      <td>{row.key}</td>
                      <td>
                        <input
                          inputMode="decimal"
                          value={edits[row.key] ?? String(row.value)}
                          onChange={(e) => {
                            setSaved(false);
                            setEdits({ ...edits, [row.key]: e.target.value });
                          }}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <button type="button" onClick={() => void save(rows)}>
              {t.monitorThresholdsSave}
            </button>
            {saved && <span className="card-sub"> {t.monitorThresholdsSaved}</span>}
          </>
        )}
      </Async>
    </section>
  );
}

export function AdminControlTower() {
  const [tower, reload] = useApi(() => api.get<ControlTower>("/admin/control-tower"));
  return (
    <Screen title={t.controlTowerTitle}>
      <Async value={tower} reload={reload}>
        {(c) => <SystemHealth c={c} />}
      </Async>
      <AccuracySection />
      <TaskHistorySection />
      <PagePerfSection />
      <TrafficSection />
      <HistoricalSection />
      <section className="card pad">
        <h2>{t.monitorAlertsSection}</h2>
        <Link to="/admin/alerts">{t.monitorViewAllAlerts}</Link>
      </section>
      <ThresholdsSection />
    </Screen>
  );
}
