import { useState } from "react";
import { api } from "../api/client";
import type { WriteTool, Level, GlobalState, Job, LlmUsageRow, Mode, Page, Tenant } from "../api/types";
import { Async, Chip, Empty, ReasonDialog, Screen } from "../components/common";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { formatIst } from "../format";
import { t, type Key } from "../i18n";

type Pending = { tenant: Tenant; kind: "approve" | "pause" | "resume" | "mode" | "limit" | "autonomy"; mode?: Mode; limit?: number | null; tool?: WriteTool; level?: Level };

export function AdminTenants() {
  const toast = useToast();
  const [list, reload] = useApi(() => api.get<Page<Tenant>>("/admin/tenants"));
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
                        {tenant.name}
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
