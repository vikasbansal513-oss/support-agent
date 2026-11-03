// Usage Intelligence: Billing Overview / Client-wise / Usage Ledger / Reports / Alerts & Limits.
// A new tab inside the existing Admin -> Billing page (AdminBilling, in Billing.tsx) -- kept in
// its own file so the already-large Billing.tsx does not need touching beyond adding the tab
// button + a conditional render. Nothing here reads or writes the existing period/invoice flow.
import { useState } from "react";
import { api, downloadFile } from "../api/client";
import type {
  BillingExportSyncStatus,
  BillingOverview,
  ClientBillingDetail,
  ClientBillingSummary,
  DatePreset,
  LedgerEventType,
  ReportOut,
  ReportType,
  UsageLedgerOut,
  UsageLimitsOut,
  UsageLimitStatusesOut,
  UsageMetric,
} from "../api/types";
import { Async, Chip, Empty, StatCard } from "../components/common";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { t } from "../i18n";

function formatInr(value: string | null | undefined): string {
  if (value === null || value === undefined) return "-";
  const n = Number(value);
  if (Number.isNaN(n)) return "-";
  return `₹${n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatPercent(value: string): string {
  const n = Number(value);
  return Number.isNaN(n) ? "-" : `${n}%`;
}

type IntelTab = "overview" | "clients" | "ledger" | "reports" | "alerts";

const PRESETS: { value: DatePreset; label: string }[] = [
  { value: "today", label: t.intelRangeToday },
  { value: "this_week", label: t.intelRangeWeek },
  { value: "this_month", label: t.intelRangeMonth },
  { value: "previous_month", label: t.intelRangePrevMonth },
  { value: "custom", label: t.intelRangeCustom },
];

function RangePicker({
  preset,
  setPreset,
  start,
  setStart,
  end,
  setEnd,
}: {
  preset: DatePreset;
  setPreset: (value: DatePreset) => void;
  start: string;
  setStart: (value: string) => void;
  end: string;
  setEnd: (value: string) => void;
}) {
  return (
    <div className="row">
      <label className="field">
        <span className="field-label">Range</span>
        <select value={preset} onChange={(event) => setPreset(event.target.value as DatePreset)}>
          {PRESETS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
      </label>
      {preset === "custom" && (
        <>
          <label className="field">
            <span className="field-label">{t.intelFrom}</span>
            <input type="date" value={start} onChange={(event) => setStart(event.target.value)} />
          </label>
          <label className="field">
            <span className="field-label">{t.intelTo}</span>
            <input type="date" value={end} onChange={(event) => setEnd(event.target.value)} />
          </label>
        </>
      )}
    </div>
  );
}

function rangeQuery(preset: DatePreset, start: string, end: string): string {
  const params = new URLSearchParams({ preset });
  if (preset === "custom") {
    if (start) params.set("start", start);
    if (end) params.set("end", end);
  }
  return params.toString();
}

function OverviewPanel() {
  const [preset, setPreset] = useState<DatePreset>("this_month");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [data, reload] = useApi(
    () => api.get<BillingOverview>(`/admin/billing/overview?${rangeQuery(preset, start, end)}`),
    [preset, start, end],
  );
  return (
    <div>
      <RangePicker preset={preset} setPreset={setPreset} start={start} setStart={setStart} end={end} setEnd={setEnd} />
      <Async value={data} reload={reload}>
        {(o) => (
          <>
            <div className="stat-grid">
              <StatCard label={t.intelRevenue} value={formatInr(o.total_revenue_inr)} icon="package" />
              <StatCard label={t.intelCost} value={formatInr(o.total_client_usage_cost_inr)} icon="file" />
              <StatCard
                label={t.intelProfit}
                value={formatInr(o.gross_profit_inr)}
                tone={Number(o.gross_profit_inr) >= 0 ? "ok" : "warn"}
                icon="arrows"
              />
              <StatCard label={t.intelMargin} value={formatPercent(o.profit_margin_percent)} icon="info" />
              <StatCard label={t.intelActiveClients} value={o.active_clients} icon="users" />
              <StatCard label={t.intelUnallocated} value={formatInr(o.unallocated_infra_cost_inr)} tone="warn" icon="bell" />
            </div>
            <div className="card">
              <h3>{t.billBreakdownTitle}</h3>
              <table>
                <tbody>
                  <tr>
                    <td>{t.intelCostLlm}</td>
                    <td>{formatInr(o.costs.llm_cost_inr)}</td>
                  </tr>
                  <tr>
                    <td>{t.intelCostCloud}</td>
                    <td>{formatInr(o.costs.cloudrun_cost_inr)}</td>
                  </tr>
                  <tr>
                    <td>{t.intelCostSpeech}</td>
                    <td>{formatInr(o.costs.speech_cost_inr)}</td>
                  </tr>
                  <tr>
                    <td>{t.intelCostPlivo}</td>
                    <td>{formatInr(o.costs.plivo_cost_inr)}</td>
                  </tr>
                  <tr>
                    <td>{t.intelCostGcp}</td>
                    <td>{formatInr(o.costs.other_gcp_cost_inr)}</td>
                  </tr>
                </tbody>
              </table>
              {o.unpriced_llm_models.length > 0 && (
                <p className="banner">
                  {t.intelUnpriced}: {o.unpriced_llm_models.join(", ")}
                </p>
              )}
              <p className="muted">{o.note}</p>
            </div>
          </>
        )}
      </Async>
    </div>
  );
}

function ClientDetailView({ tenantId, preset, start, end, onBack }: { tenantId: string; preset: DatePreset; start: string; end: string; onBack: () => void }) {
  const [data, reload] = useApi(
    () => api.get<ClientBillingDetail>(`/admin/billing/clients/${tenantId}?${rangeQuery(preset, start, end)}`),
    [tenantId, preset, start, end],
  );
  return (
    <div>
      <button type="button" className="secondary" onClick={onBack}>
        {t.intelBackToClients}
      </button>
      <Async value={data} reload={reload}>
        {(c) => (
          <>
            <h2>{c.client.tenant_name}</h2>
            <div className="stat-grid">
              <StatCard label={t.intelMessages} value={c.messages.total} icon="chat" />
              <StatCard label={t.intelMessagesIn} value={c.messages.incoming} icon="arrows" />
              <StatCard label={t.intelMessagesOut} value={c.messages.outgoing} icon="arrows" />
            </div>
            <div className="card">
              <h3>{t.intelAgentPerf}</h3>
              <div className="stat-grid">
                <StatCard label={t.intelTasksTotal} value={c.agent_performance.total_tasks} icon="check-square" />
                <StatCard label={t.intelTasksDone} value={c.agent_performance.completed} icon="check-circle" tone="ok" />
                <StatCard label={t.intelTasksFailed} value={c.agent_performance.failed} icon="bell" tone="warn" />
                <StatCard label={t.intelTasksPending} value={c.agent_performance.pending} icon="clock" />
                <StatCard label={t.intelSuccessRate} value={formatPercent(c.agent_performance.success_rate_percent)} icon="info" />
                <StatCard label={t.intelAvgExec} value={c.agent_performance.avg_execution_seconds} icon="clock" />
                <StatCard label={t.intelRetries} value={c.agent_performance.retry_count} icon="arrows" />
                <StatCard label={t.intelCostPerTask} value={formatInr(c.agent_performance.cost_per_successful_task_inr)} icon="file" />
              </div>
            </div>
            <div className="card">
              <h3>{t.intelLlmUsage}</h3>
              {c.llm_usage.length === 0 ? (
                <Empty>-</Empty>
              ) : (
                <table>
                  <thead>
                    <tr>
                      <th>Provider</th>
                      <th>Model</th>
                      <th>Calls</th>
                      <th>Input tokens</th>
                      <th>Output tokens</th>
                      <th>Cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {c.llm_usage.map((line) => (
                      <tr key={`${line.provider}-${line.model}`}>
                        <td>{line.provider}</td>
                        <td>{line.model}</td>
                        <td>{line.api_calls}</td>
                        <td>{line.input_tokens}</td>
                        <td>{line.output_tokens}</td>
                        <td>
                          {formatInr(line.total_cost_inr)} {!line.priced && <Chip tone="warn">unpriced</Chip>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
            <div className="card">
              <h3>{t.intelGcpUsage}</h3>
              {c.gcp_usage.length === 0 ? (
                <Empty>-</Empty>
              ) : (
                <table>
                  <thead>
                    <tr>
                      <th>Category</th>
                      <th>Quantity</th>
                      <th>Unit</th>
                      <th>Cost (USD)</th>
                      <th>Cost (INR)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {c.gcp_usage.map((line) => (
                      <tr key={line.category}>
                        <td>{line.category}</td>
                        <td>{line.quantity}</td>
                        <td>{line.unit}</td>
                        <td>${line.cost_usd}</td>
                        <td>{formatInr(line.cost_inr)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
            <div className="card">
              <h3>{t.intelOtherCosts}</h3>
              <table>
                <tbody>
                  <tr>
                    <td>Plivo outbound</td>
                    <td>{formatInr(c.other_costs.plivo_outbound_inr)}</td>
                  </tr>
                  <tr>
                    <td>Plivo inbound</td>
                    <td>{formatInr(c.other_costs.plivo_inbound_inr)}</td>
                  </tr>
                  <tr>
                    <td>Plivo number rental</td>
                    <td>{formatInr(c.other_costs.plivo_number_rental_inr)}</td>
                  </tr>
                  <tr>
                    <td>STT</td>
                    <td>{formatInr(c.other_costs.stt_cost_inr)}</td>
                  </tr>
                  <tr>
                    <td>TTS</td>
                    <td>{formatInr(c.other_costs.tts_cost_inr)}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div className="card">
              <h3>{t.intelFinancialSummary}</h3>
              <div className="stat-grid">
                <StatCard label="Operating cost" value={formatInr(c.financial_summary.total_operating_cost_inr)} icon="file" />
                <StatCard label="Revenue" value={formatInr(c.financial_summary.client_revenue_inr)} icon="package" />
                <StatCard
                  label="Profit"
                  value={formatInr(c.financial_summary.gross_profit_inr)}
                  tone={Number(c.financial_summary.gross_profit_inr) >= 0 ? "ok" : "warn"}
                  icon="arrows"
                />
                <StatCard label="Margin" value={formatPercent(c.financial_summary.profit_margin_percent)} icon="info" />
              </div>
            </div>
          </>
        )}
      </Async>
    </div>
  );
}

function ClientsPanel() {
  const [preset, setPreset] = useState<DatePreset>("this_month");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [data, reload] = useApi(
    () => api.get<ClientBillingSummary[]>(`/admin/billing/clients?${rangeQuery(preset, start, end)}`),
    [preset, start, end],
  );
  if (selected) {
    return <ClientDetailView tenantId={selected} preset={preset} start={start} end={end} onBack={() => setSelected(null)} />;
  }
  return (
    <div>
      <h3>{t.intelClientsTitle}</h3>
      <p className="muted">{t.intelClientsSub}</p>
      <RangePicker preset={preset} setPreset={setPreset} start={start} setStart={setStart} end={end} setEnd={setEnd} />
      <Async value={data} reload={reload}>
        {(rows) =>
          rows.length === 0 ? (
            <Empty>-</Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Client</th>
                  <th>Status</th>
                  <th>{t.intelMessages}</th>
                  <th>{t.intelCost}</th>
                  <th>{t.intelRevenue}</th>
                  <th>{t.intelProfit}</th>
                  <th>{t.intelMargin}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.tenant_id}>
                    <td>{row.tenant_name}</td>
                    <td>
                      <Chip tone={row.billing_status === "enabled" ? "ok" : "warn"}>{row.billing_status}</Chip>
                    </td>
                    <td>{row.messages_total}</td>
                    <td>{formatInr(row.total_operating_cost_inr)}</td>
                    <td>{formatInr(row.client_revenue_inr)}</td>
                    <td>{formatInr(row.gross_profit_inr)}</td>
                    <td>{formatPercent(row.profit_margin_percent)}</td>
                    <td>
                      <button type="button" className="secondary" onClick={() => setSelected(row.tenant_id)}>
                        View
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        }
      </Async>
    </div>
  );
}

const LEDGER_TYPES: { value: LedgerEventType | ""; label: string }[] = [
  { value: "", label: t.intelLedgerAll },
  { value: "message", label: "Message" },
  { value: "llm_call", label: "LLM call" },
  { value: "agent_execution", label: "Agent execution" },
  { value: "gcp_usage", label: "GCP usage" },
  { value: "plivo_call", label: "Plivo call" },
  { value: "speech_usage", label: "Speech usage" },
];

function LedgerPanel() {
  const toast = useToast();
  const today = new Date().toISOString().slice(0, 10);
  const monthAgo = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
  const [start, setStart] = useState(monthAgo);
  const [end, setEnd] = useState(today);
  const [eventType, setEventType] = useState<LedgerEventType | "">("");
  const [data, reload] = useApi(
    () =>
      api.get<UsageLedgerOut>(
        `/admin/billing/usage-ledger?${new URLSearchParams({
          start,
          end,
          ...(eventType ? { event_type: eventType } : {}),
        }).toString()}`,
      ),
    [start, end, eventType],
  );
  const exportCsv = async () => {
    try {
      await downloadFile(
        `/admin/billing/usage-ledger/export.csv?${new URLSearchParams({
          start,
          end,
          ...(eventType ? { event_type: eventType } : {}),
          limit: "500",
        }).toString()}`,
        "usage-ledger.csv",
      );
    } catch (err) {
      toast.error(err);
    }
  };
  return (
    <div>
      <h3>{t.intelLedgerTitle}</h3>
      <p className="muted">{t.intelLedgerSub}</p>
      <div className="row">
        <label className="field">
          <span className="field-label">{t.intelFrom}</span>
          <input type="date" value={start} onChange={(event) => setStart(event.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">{t.intelTo}</span>
          <input type="date" value={end} onChange={(event) => setEnd(event.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">{t.intelLedgerEventType}</span>
          <select value={eventType} onChange={(event) => setEventType(event.target.value as LedgerEventType | "")}>
            {LEDGER_TYPES.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <button type="button" onClick={() => void exportCsv()}>
          {t.intelLedgerExport}
        </button>
      </div>
      <Async value={data} reload={reload}>
        {(out) =>
          out.rows.length === 0 ? (
            <Empty>-</Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Client</th>
                  <th>Type</th>
                  <th>Provider</th>
                  <th>Service</th>
                  <th>Quantity</th>
                  <th>Cost</th>
                  <th>Status</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {out.rows.map((row) => (
                  <tr key={row.event_id}>
                    <td>{row.tenant_name}</td>
                    <td>{row.event_type}</td>
                    <td>{row.service_provider}</td>
                    <td>{row.service_name}</td>
                    <td>
                      {row.quantity} {row.unit}
                    </td>
                    <td>{row.actual_cost_inr ? formatInr(row.actual_cost_inr) : row.estimated_cost_inr ? `~${formatInr(row.estimated_cost_inr)}` : "-"}</td>
                    <td>{row.status}</td>
                    <td>{new Date(row.started_at).toLocaleString("en-IN")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        }
      </Async>
    </div>
  );
}

const REPORT_TYPES: { value: ReportType; label: string }[] = [
  { value: "client_usage", label: "Client usage" },
  { value: "llm", label: "LLM" },
  { value: "gcp", label: "Google Cloud" },
  { value: "plivo", label: "Plivo" },
  { value: "agent_performance", label: "Agent performance" },
  { value: "profitability", label: "Client profitability" },
  { value: "actual_vs_estimated", label: "Actual vs estimated" },
  { value: "unallocated", label: "Unallocated cost" },
];

function ReportsPanel() {
  const toast = useToast();
  const [reportType, setReportType] = useState<ReportType>("client_usage");
  const [preset, setPreset] = useState<DatePreset>("this_month");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [data, reload] = useApi(
    () => api.get<ReportOut>(`/admin/billing/reports/${reportType}?${rangeQuery(preset, start, end)}`),
    [reportType, preset, start, end],
  );
  const exportCsv = async () => {
    try {
      await downloadFile(`/admin/billing/reports/${reportType}/export.csv?${rangeQuery(preset, start, end)}`, `${reportType}.csv`);
    } catch (err) {
      toast.error(err);
    }
  };
  return (
    <div>
      <h3>{t.intelReportsTitle}</h3>
      <p className="muted">{t.intelReportsSub}</p>
      <div className="row">
        <label className="field">
          <span className="field-label">Report</span>
          <select value={reportType} onChange={(event) => setReportType(event.target.value as ReportType)}>
            {REPORT_TYPES.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <RangePicker preset={preset} setPreset={setPreset} start={start} setStart={setStart} end={end} setEnd={setEnd} />
        <button type="button" onClick={() => void exportCsv()}>
          {t.intelReportExport}
        </button>
      </div>
      <Async value={data} reload={reload}>
        {(report) =>
          report.rows.length === 0 ? (
            <Empty>-</Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  {report.columns.map((c) => (
                    <th key={c}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {report.rows.map((row, i) => (
                  <tr key={i}>
                    {row.map((cell, j) => (
                      <td key={j}>{cell}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )
        }
      </Async>
    </div>
  );
}

const METRICS: { value: UsageMetric; label: string }[] = [
  { value: "messages_month", label: t.intelMetric_messages_month },
  { value: "total_cost_inr_month", label: t.intelMetric_total_cost_inr_month },
  { value: "llm_cost_inr_month", label: t.intelMetric_llm_cost_inr_month },
];

function AlertsPanel() {
  const toast = useToast();
  const [clients, clientsReload] = useApi(() => api.get<ClientBillingSummary[]>("/admin/billing/clients?preset=this_month"), []);
  const [limits, limitsReload] = useApi(() => api.get<UsageLimitsOut>("/admin/billing/usage-limits"), []);
  const [statuses, statusesReload] = useApi(() => api.get<UsageLimitStatusesOut>("/admin/billing/usage-limits/status"), []);
  const [exportStatus] = useApi(() => api.get<BillingExportSyncStatus>("/admin/billing/gcp/export-status"), []);
  const [tenantId, setTenantId] = useState("");
  const [metric, setMetric] = useState<UsageMetric>("messages_month");
  const [limitValue, setLimitValue] = useState("");
  const [busy, setBusy] = useState(false);

  const save = async () => {
    if (!tenantId || !limitValue) return;
    setBusy(true);
    try {
      await api.put("/admin/billing/usage-limits", {
        tenant_id: tenantId,
        metric,
        limit_value: limitValue,
        active: true,
      });
      toast.info(t.intelLimitSaved);
      setLimitValue("");
      limitsReload();
      statusesReload();
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <h3>{t.intelAlertsTitle}</h3>
      <p className="muted">{t.intelAlertsSub}</p>
      <div className="card">
        <div className="row">
          <label className="field">
            <span className="field-label">{t.intelLimitClient}</span>
            <Async value={clients} reload={clientsReload}>
              {(rows) => (
                <select value={tenantId} onChange={(event) => setTenantId(event.target.value)}>
                  <option value="">-</option>
                  {rows.map((row) => (
                    <option key={row.tenant_id} value={row.tenant_id}>
                      {row.tenant_name}
                    </option>
                  ))}
                </select>
              )}
            </Async>
          </label>
          <label className="field">
            <span className="field-label">{t.intelLimitMetric}</span>
            <select value={metric} onChange={(event) => setMetric(event.target.value as UsageMetric)}>
              {METRICS.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field-label">{t.intelLimitValue}</span>
            <input type="number" min="0" step="0.01" value={limitValue} onChange={(event) => setLimitValue(event.target.value)} />
          </label>
          <button type="button" disabled={busy || !tenantId || !limitValue} onClick={() => void save()}>
            {t.intelLimitSave}
          </button>
        </div>
      </div>
      <div className="card">
        <h4>{t.intelLimitsExisting}</h4>
        <Async value={limits} reload={limitsReload}>
          {(out) =>
            out.limits.length === 0 ? (
              <Empty>-</Empty>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>{t.intelLimitClient}</th>
                    <th>{t.intelLimitMetric}</th>
                    <th>{t.intelLimitValue}</th>
                    <th>{t.intelLimitActive}</th>
                  </tr>
                </thead>
                <tbody>
                  {out.limits.map((lim) => (
                    <tr key={lim.id}>
                      <td>{lim.tenant_id}</td>
                      <td>{lim.metric}</td>
                      <td>{lim.limit_value}</td>
                      <td>{lim.active ? "yes" : "no"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          }
        </Async>
      </div>
      <div className="card">
        <h4>{t.intelStatusTitle}</h4>
        <Async value={statuses} reload={statusesReload}>
          {(out) =>
            out.statuses.length === 0 ? (
              <Empty>-</Empty>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Client</th>
                    <th>Metric</th>
                    <th>Used</th>
                    <th>Limit</th>
                    <th>%</th>
                  </tr>
                </thead>
                <tbody>
                  {out.statuses.map((s, i) => (
                    <tr key={i}>
                      <td>{s.tenant_name}</td>
                      <td>{s.metric}</td>
                      <td>{s.current_value}</td>
                      <td>{s.limit_value}</td>
                      <td>
                        <Chip tone={s.breached_100 ? "urgent" : s.breached_90 ? "warn" : s.breached_70 ? "warn" : "ok"}>{s.percent_used}%</Chip>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          }
        </Async>
      </div>
      <div className="card">
        <h4>{t.intelExportStatusTitle}</h4>
        <Async value={exportStatus}>
          {(status) => (
            <table>
              <tbody>
                <tr>
                  <td>Status</td>
                  <td>
                    <Chip tone={status.status === "connected" ? "ok" : "warn"}>
                      {status.status === "connected" ? t.intelExportStatusConnected : t.intelExportStatusNotConnected}
                    </Chip>
                  </td>
                </tr>
                <tr>
                  <td>{t.intelExportLastSync}</td>
                  <td>{status.last_sync_at ? new Date(status.last_sync_at).toLocaleString("en-IN") : t.intelExportNever}</td>
                </tr>
                <tr>
                  <td>{t.intelExportRows}</td>
                  <td>{status.last_sync_rows}</td>
                </tr>
                <tr>
                  <td>{t.intelExportBatches}</td>
                  <td>{status.import_batches}</td>
                </tr>
                {status.last_error && (
                  <tr>
                    <td>{t.intelExportError}</td>
                    <td>{status.last_error}</td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </Async>
        <p className="muted">{exportStatus.state === "data" ? exportStatus.data.note : ""}</p>
      </div>
    </div>
  );
}

// Embedded as a tab's content inside AdminBilling (Billing.tsx) -- no outer Screen/<h1> of its
// own, since the parent page already provides one. Has its own inner sub-tab row.
export function BillingIntel() {
  const [tab, setTab] = useState<IntelTab>("overview");
  return (
    <div className="intel-panel">
      <div className="filters" role="tablist">
        {(
          [
            ["overview", t.intelTabOverview],
            ["clients", t.intelTabClients],
            ["ledger", t.intelTabLedger],
            ["reports", t.intelTabReports],
            ["alerts", t.intelTabAlerts],
          ] as [IntelTab, string][]
        ).map(([key, label]) => (
          <button key={key} type="button" role="tab" className={tab === key ? "filter-tab active" : "filter-tab"} onClick={() => setTab(key)}>
            {label}
          </button>
        ))}
      </div>
      {tab === "overview" && <OverviewPanel />}
      {tab === "clients" && <ClientsPanel />}
      {tab === "ledger" && <LedgerPanel />}
      {tab === "reports" && <ReportsPanel />}
      {tab === "alerts" && <AlertsPanel />}
    </div>
  );
}
