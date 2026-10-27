import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { WriteTool, Health, Level, GlobalState, Job, LlmProvider, LlmSettings, LlmTestResult, LlmUsageRow, Mode, Overview, Page, Tenant } from "../api/types";
import { Async, Chip, Empty, Field, ReasonDialog, Screen, StatCard } from "../components/common";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { formatIst } from "../format";
import { t, type Key } from "../i18n";

type Pending = { tenant: Tenant; kind: "approve" | "pause" | "resume" | "mode" | "limit" | "autonomy"; mode?: Mode; limit?: number | null; tool?: WriteTool; level?: Level };

export function AdminOverview() {
  const [overview, reload] = useApi(() => api.get<Overview>("/admin/overview"));
  const [health, reloadHealth] = useApi(() => api.get<Health>("/admin/health"));
  const maxCalls = (rows: { calls: number }[]) => Math.max(1, ...rows.map((r) => r.calls));
  return (
    <Screen title={t.overviewTitle}>
      <Async value={overview} reload={reload}>
        {(o) => (
          <>
            <div className="grid">
              <StatCard label={t.totalBrands} value={o.tenants_total} />
              <StatCard label={t.activeBrands} value={o.tenants_active} tone="ok" />
              <StatCard label={t.pendingBrands} value={o.tenants_pending} tone={o.tenants_pending > 0 ? "warn" : undefined} />
              <StatCard label={t.pausedBrands} value={o.tenants_paused} />
              <StatCard label={t.openHandoffs} value={o.handoffs_open} tone={o.handoffs_open > 0 ? "warn" : undefined} />
              <StatCard label={t.failedJobs} value={o.jobs_failed} tone={o.jobs_failed > 0 ? "warn" : undefined} />
              <StatCard label={t.todayCalls} value={o.today_calls} />
              <StatCard label={t.todayCost} value={`₹${(o.today_cost_micro / 1_000_000).toFixed(2)}`} />
            </div>
            <h2>{t.callsLast7Days}</h2>
            {o.calls_by_day.length === 0 ? (
              <Empty>{t.noRows}</Empty>
            ) : (
              <div className="row" style={{ alignItems: "flex-end", gap: 8 }}>
                {o.calls_by_day.map((d) => (
                  <div key={d.day} style={{ textAlign: "center" }}>
                    <div style={{ height: 100 * (d.calls / maxCalls(o.calls_by_day)), width: 24, background: "currentColor", opacity: 0.6 }} />
                    <small>{d.day.slice(5)}</small>
                    <br />
                    <small>{d.calls}</small>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </Async>
      <h2>{t.systemHealthTitle}</h2>
      <Async value={health} reload={reloadHealth}>
        {(h) => (
          <div className="row">
            <Chip tone={h.db === "ok" ? "ok" : "urgent"}>{t.healthDb}: {h.db}</Chip>
            {h.worker_seconds_since === null ? (
              <Chip tone="urgent">{t.healthWorker}: {t.healthWorkerNeverSeen}</Chip>
            ) : (
              <Chip tone={h.worker_seconds_since > 180 ? "urgent" : "ok"}>
                {t.healthWorker}: {h.worker_seconds_since} {t.healthWorkerSecondsAgo}
              </Chip>
            )}
            <Chip tone={h.queue_depth > 0 ? "warn" : "ok"}>{t.healthQueueDepth}: {h.queue_depth}</Chip>
            <Chip tone={h.jobs_failed_today > 0 ? "urgent" : "ok"}>{t.healthFailedToday}: {h.jobs_failed_today}</Chip>
          </div>
        )}
      </Async>
    </Screen>
  );
}

export function AdminTenants() {
  const toast = useToast();
  const [q, setQ] = useState("");
  const [applied, setApplied] = useState("");
  const [list, reload] = useApi(
    () => api.get<Page<Tenant>>(`/admin/tenants${applied ? `?q=${encodeURIComponent(applied)}` : ""}`),
    [applied],
  );
  const [pending, setPending] = useState<Pending | null>(null);
  const [limitText, setLimitText] = useState<Record<string, string>>({});
  const [auto, setAuto] = useState<{ tool: WriteTool; level: Level }>({ tool: "address_update", level: "auto" });

  const confirm = async (reason: string) => {
    if (!pending) return;
    const { tenant, kind } = pending;
    try {
      if (kind === "mode") await api.put(`/admin/tenants/${tenant.id}/mode`, { mode: pending.mode, reason });
      else if (kind === "autonomy") await api.put(`/admin/tenants/${tenant.id}/autonomy`, { tool: pending.tool, level: pending.level, reason });
      else if (kind === "limit") await api.put(`/admin/tenants/${tenant.id}/usage-limit`, { usage_override_limit: pending.limit ?? null, reason });
      else await api.post(`/admin/tenants/${tenant.id}/${kind}`, { reason });
      setPending(null);
      reload();
    } catch (err) {
      toast.error(err);
      setPending(null);
    }
  };

  return (
    <Screen title={t.tenantsTitle}>
      <form className="row" onSubmit={(event) => { event.preventDefault(); setApplied(q.trim()); }}>
        <input aria-label={t.searchBrands} placeholder={t.searchBrands} value={q} onChange={(event) => setQ(event.target.value)} />
        <button type="submit" className="secondary">{t.searchBrands}</button>
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
                    <th>{t.brandName}</th>
                    <th>Status</th>
                    <th>{t.modeLabel}</th>
                    <th>{t.usageLimit}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((tenant) => (
                    <tr key={tenant.id}>
                      <td>
                        <Link to={`/admin/tenants/${tenant.id}`}>{tenant.name}</Link>
                        <br />
                        <small>{tenant.slug}</small>
                      </td>
                      <td>
                        <Chip tone={tenant.status === "active" ? "ok" : "warn"}>{tenant.status}</Chip>
                        {tenant.agent_paused && <Chip tone="urgent">{t.paused}</Chip>}
                      </td>
                      <td>
                        <select
                          aria-label={`${t.setMode} ${tenant.name}`}
                          value={tenant.mode}
                          onChange={(event) => setPending({ tenant, kind: "mode", mode: event.target.value as Mode })}
                        >
                          {(["shadow", "draft", "auto"] as Mode[]).map((m) => (
                            <option key={m} value={m}>
                              {t[`mode_${m}` as Key]}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <input
                          aria-label={`${t.usageLimit} ${tenant.name}`}
                          inputMode="numeric"
                          placeholder={String(tenant.included_conversations)}
                          value={limitText[tenant.id] ?? (tenant.usage_override_limit === null ? "" : String(tenant.usage_override_limit))}
                          onChange={(event) => setLimitText({ ...limitText, [tenant.id]: event.target.value })}
                        />
                        <button
                          type="button"
                          className="secondary"
                          onClick={() => {
                            const raw = (limitText[tenant.id] ?? "").trim();
                            setPending({ tenant, kind: "limit", limit: raw === "" ? null : Number(raw) });
                          }}
                        >
                          {t.save}
                        </button>
                      </td>
                      <td>
                        <div className="row">
                          <select aria-label={`${t.adminAutonomy} tool ${tenant.name}`} value={auto.tool} onChange={(event) => setAuto({ ...auto, tool: event.target.value as WriteTool })}>
                            {(["address_update", "order_cancel", "exchange_request", "refund_create"] as WriteTool[]).map((tool) => (
                              <option key={tool} value={tool}>
                                {t[`tool_${tool}` as Key]}
                              </option>
                            ))}
                          </select>
                          <select aria-label={`${t.adminAutonomy} level ${tenant.name}`} value={auto.level} onChange={(event) => setAuto({ ...auto, level: event.target.value as Level })}>
                            {(["off", "approval", "auto"] as Level[]).map((level) => (
                              <option key={level} value={level}>
                                {t[`level_${level}` as Key]}
                              </option>
                            ))}
                          </select>
                          <button type="button" className="secondary" onClick={() => setPending({ tenant, kind: "autonomy", tool: auto.tool, level: auto.level })}>
                            {t.adminAutonomy}
                          </button>
                          {tenant.status === "pending_approval" && (
                            <button type="button" onClick={() => setPending({ tenant, kind: "approve" })}>
                              {t.approveTenant}
                            </button>
                          )}
                          {tenant.status === "active" && (
                            <button type="button" className="secondary" onClick={() => setPending({ tenant, kind: "pause" })}>
                              {t.pauseTenant}
                            </button>
                          )}
                          {tenant.status === "paused" && (
                            <button type="button" onClick={() => setPending({ tenant, kind: "resume" })}>
                              {t.resumeTenant}
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Async>
      {pending && <ReasonDialog title={`${pending.tenant.name}: ${pending.kind}`} onConfirm={(reason) => void confirm(reason)} onCancel={() => setPending(null)} />}
    </Screen>
  );
}

function GlobalPauseButton() {
  const toast = useToast();
  const [state, reload] = useApi(() => api.get<GlobalState>("/admin/global-pause"));
  const [asking, setAsking] = useState(false);
  return (
    <Async value={state} reload={reload}>
      {(s) => {
        const paused = s.status === "paused";
        const confirm = async (reason: string) => {
          try {
            await api.post(paused ? "/admin/global-resume" : "/admin/global-pause", { reason });
            setAsking(false);
            reload();
          } catch (err) {
            toast.error(err);
            setAsking(false);
          }
        };
        return (
          <section className="card">
            <h2>{t.globalPauseTitle}</h2>
            <p>{paused ? t.globalIsPaused : t.globalIsRunning}</p>
            <button type="button" className={paused ? undefined : "danger"} onClick={() => setAsking(true)}>
              {paused ? t.globalResume : t.globalPause}
            </button>
            {asking && <ReasonDialog title={paused ? t.globalResume : t.globalPause} onConfirm={(reason) => void confirm(reason)} onCancel={() => setAsking(false)} />}
          </section>
        );
      }}
    </Async>
  );
}

export function AdminOps() {
  const [jobs, reloadJobs] = useApi(() => api.get<Page<Job>>("/admin/jobs"));
  const [usage, reloadUsage] = useApi(() => api.get<LlmUsageRow[]>("/admin/llm-usage"));
  return (
    <Screen title={t.opsTitle}>
      <GlobalPauseButton />
      <LlmSettingsPanel />
      <h2>{t.jobs}</h2>
      <Async value={jobs} reload={reloadJobs}>
        {(page) =>
          page.items.length === 0 ? (
            <Empty>{t.noRows}</Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Kind</th>
                    <th>Status</th>
                    <th>Attempts</th>
                    <th>Error</th>
                    <th>Time</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((j) => (
                    <tr key={j.id}>
                      <td>{j.kind}</td>
                      <td>
                        <Chip tone={j.status === "needs_review" || j.status === "failed" ? "urgent" : undefined}>{j.status}</Chip>
                      </td>
                      <td>{j.attempts}</td>
                      <td>{j.error_code ?? "-"} {j.error_id ?? ""}</td>
                      <td>{formatIst(j.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Async>
      <h2>{t.llmUsage}</h2>
      <Async value={usage} reload={reloadUsage}>
        {(rows) =>
          rows.length === 0 ? (
            <Empty>{t.noRows}</Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Din</th>
                    <th>Calls</th>
                    <th>In</th>
                    <th>Out</th>
                    <th>Cost (micro)</th>
                    <th>p95 ms</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={`${r.tenant_id}-${r.day}`}>
                      <td>{r.day}</td>
                      <td>{r.calls}</td>
                      <td>{r.input_tokens}</td>
                      <td>{r.output_tokens}</td>
                      <td>{r.est_cost_micro ?? "-"}</td>
                      <td>{r.p95_latency_ms ?? "-"}</td>
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

const LLM_PROVIDERS: [LlmProvider, string][] = [
  ["anthropic", "Anthropic (Claude)"],
  ["groq", "Groq"],
  ["openai", "OpenAI"],
  ["openrouter", "OpenRouter"],
  ["openai_compatible", "Custom (OpenAI-compatible)"],
];

function LlmSettingsPanel() {
  const [state, reload] = useApi(() => api.get<LlmSettings>("/admin/llm-settings"));
  return (
    <Async value={state} reload={reload}>
      {(current) => <LlmSettingsForm key={`${current.provider}-${current.model}-${current.updated_at ?? ""}`} current={current} onSaved={reload} />}
    </Async>
  );
}

function LlmSettingsForm({ current, onSaved }: { current: LlmSettings; onSaved: () => void }) {
  const toast = useToast();
  const [provider, setProvider] = useState<LlmProvider>(current.provider);
  const [model, setModel] = useState(current.model);
  const [baseUrl, setBaseUrl] = useState(current.base_url);
  const [apiKey, setApiKey] = useState("");
  const [asking, setAsking] = useState(false);
  const [testResult, setTestResult] = useState<LlmTestResult | null>(null);

  const choice = () => ({
    provider,
    model: model.trim(),
    base_url: baseUrl.trim(),
    ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
  });

  const runTest = async () => {
    setTestResult(null);
    try {
      setTestResult(await api.post<LlmTestResult>("/admin/llm-settings/test", choice()));
    } catch (err) {
      toast.error(err);
    }
  };

  const save = async (reason: string) => {
    try {
      await api.put<LlmSettings>("/admin/llm-settings", { ...choice(), reason });
      setAsking(false);
      setApiKey("");
      toast.info(t.llmSaved);
      onSaved();
    } catch (err) {
      toast.error(err);
      setAsking(false);
    }
  };

  return (
    <section className="card">
      <h2>{t.llmSettingsTitle}</h2>
      <p>
        <Chip tone={current.source === "dashboard" ? "ok" : undefined}>{current.source === "dashboard" ? t.llmSourceDashboard : t.llmSourceDeploy}</Chip>{" "}
        <Chip tone={current.has_key ? "ok" : "warn"}>{current.has_key ? t.llmKeySet : t.llmKeyMissing}</Chip>
      </p>
      <label className="field">
        <span>{t.llmProvider}</span>
        <select aria-label={t.llmProvider} value={provider} onChange={(event) => setProvider(event.target.value as LlmProvider)}>
          {LLM_PROVIDERS.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <Field label={t.llmModel} value={model} onChange={setModel} />
      <Field label={t.llmBaseUrl} value={baseUrl} onChange={setBaseUrl} placeholder="https://api.example.com/v1" />
      <Field label={t.llmApiKey} value={apiKey} onChange={setApiKey} type="password" autoComplete="off" placeholder={t.llmKeyKeep} />
      <div className="row">
        <button type="button" className="secondary" onClick={() => void runTest()} disabled={model.trim() === ""}>
          {t.llmTest}
        </button>
        <button type="button" onClick={() => setAsking(true)} disabled={model.trim() === ""}>
          {t.llmSave}
        </button>
      </div>
      {testResult && (
        <p role="status">
          <Chip tone={testResult.ok ? "ok" : "urgent"}>{testResult.ok ? `${t.llmTestOk} (${testResult.latency_ms} ms)` : `${t.llmTestFail}: ${testResult.error ?? ""}`}</Chip>
        </p>
      )}
      {asking && <ReasonDialog title={t.llmSave} onConfirm={(reason) => void save(reason)} onCancel={() => setAsking(false)} />}
    </section>
  );
}
