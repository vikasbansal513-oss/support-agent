import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type {
  BillingLlmRate,
  BillingPeriod,
  BillingPeriodRow,
  BillingProfile,
  BillingRates,
  BillingSettings,
  GenerateResult,
  Invoice,
  InvoiceLine,
  SpeechProvider,
  SpeechRate,
  SpeechRatesOut,
} from "../api/types";
import { GST_STATES, flagText, isPositiveDecimal, paiseToInput, previousMonthIst, stateName, tokens } from "../billing";
import { Async, Chip, Empty, Field, Screen } from "../components/common";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { formatPaise, rupeesToPaise } from "../format";
import { t, type Key } from "../i18n";

function StateSelect({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="field">
      <span>{label}</span>
      <select aria-label={label} value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">-</option>
        {GST_STATES.map(([code, name]) => (
          <option key={code} value={code}>
            {name} ({code})
          </option>
        ))}
      </select>
    </label>
  );
}

function hoursText(seconds: number): string {
  return (seconds / 3600).toFixed(2);
}

function minutesText(seconds: number): string {
  return (seconds / 60).toFixed(2);
}

type OverviewTab = "overview" | "invoices" | "usage";

export function AdminBilling() {
  const [period, setPeriod] = useState(previousMonthIst());
  const [tab, setTab] = useState<OverviewTab>("overview");
  const [data, reload] = useApi(() => api.get<BillingPeriod>(`/admin/billing/periods/${period}`), [period]);
  return (
    <Screen title={t.billTitle}>
      <div className="row">
        <label className="field">
          <span>{t.billPeriod}</span>
          <input
            type="month"
            aria-label={t.billPeriod}
            value={period}
            onChange={(event) => {
              if (/^\d{4}-\d{2}$/.test(event.target.value)) setPeriod(event.target.value);
            }}
          />
        </label>
        <Link to="/admin/billing/settings">{t.billSettingsLink}</Link>
      </div>
      <div className="tabs" role="tablist">
        <button type="button" role="tab" className={tab === "overview" ? "active" : ""} onClick={() => setTab("overview")}>
          {t.billTabOverview}
        </button>
        <button type="button" role="tab" className={tab === "invoices" ? "active" : ""} onClick={() => setTab("invoices")}>
          {t.billTabInvoices}
        </button>
        <button type="button" role="tab" className={tab === "usage" ? "active" : ""} onClick={() => setTab("usage")}>
          {t.billTabUsage}
        </button>
      </div>
      <Async value={data} reload={reload}>
        {(current) => (
          <PeriodView
            key={`${current.period}-${current.usd_inr}-${current.cloudrun_actual_paise}-${current.telephony_actual_paise}`}
            data={current}
            reload={reload}
            tab={tab}
          />
        )}
      </Async>
    </Screen>
  );
}

function PeriodView({ data, reload, tab }: { data: BillingPeriod; reload: () => void; tab: OverviewTab }) {
  const toast = useToast();
  const [fx, setFx] = useState(data.usd_inr ? String(Number(data.usd_inr)) : "");
  const [actual, setActual] = useState(data.cloudrun_actual_paise === null ? "" : paiseToInput(data.cloudrun_actual_paise));
  const [telActual, setTelActual] = useState(data.telephony_actual_paise === null ? "" : paiseToInput(data.telephony_actual_paise));
  const [busy, setBusy] = useState(false);
  const [invoiceFilter, setInvoiceFilter] = useState<"all" | "draft" | "final">("all");

  const fxValid = fx.trim() === "" || isPositiveDecimal(fx);
  const actualPaise = actual.trim() === "" ? null : rupeesToPaise(actual);
  const actualValid = actual.trim() === "" || actualPaise !== null;
  const telActualPaise = telActual.trim() === "" ? null : rupeesToPaise(telActual);
  const telActualValid = telActual.trim() === "" || telActualPaise !== null;
  const canGenerate = data.usd_inr !== null && !busy;

  const savePeriod = async () => {
    setBusy(true);
    try {
      await api.put(`/admin/billing/periods/${data.period}`, {
        usd_inr: fx.trim() === "" ? null : fx.trim(),
        cloudrun_actual_paise: actualPaise,
        telephony_actual_paise: telActualPaise,
      });
      toast.info(t.billPeriodSaved);
      reload();
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(false);
    }
  };

  const generate = async (tenantId: string | null) => {
    setBusy(true);
    try {
      const out = await api.post<GenerateResult>("/admin/billing/invoices/generate", { period: data.period, tenant_id: tenantId });
      toast.info(`${t.billGenerated}: ${out.generated}${out.skipped_final ? `, ${t.billSkippedFinal}: ${out.skipped_final}` : ""}`);
      reload();
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(false);
    }
  };

  const unpriced = Array.from(new Set(data.rows.flatMap((row) => row.unpriced_models)));
  const sum = (pick: (row: BillingPeriodRow) => number | null) => data.rows.reduce((total, row) => total + (pick(row) ?? 0), 0);
  const llmTotal = sum((r) => r.llm_paise);
  const cloudTotal = sum((r) => r.cloudrun_paise);
  const telTotal = sum((r) => r.telephony_paise);
  const feeTotal = sum((r) => r.default_platform_fee_paise);
  const grandTotal = Math.max(1, llmTotal + cloudTotal + telTotal + feeTotal);
  const pct = (value: number) => Math.round((value / grandTotal) * 100);

  const banners = (
    <>
      {data.usd_inr === null && <p className="banner">{t.billFxMissing}</p>}
      {!data.period_ended && <p className="banner">{t.billPeriodRunning}</p>}
      {unpriced.length > 0 && (
        <p className="banner">
          {t.billUnpriced} {unpriced.join(", ")} <Link to="/admin/billing/settings">{t.billSettingsLink}</Link>
        </p>
      )}
      {data.cloudrun_unallocated_paise > 0 && (
        <p className="banner">
          {t.billUnallocated} {paiseToInput(data.cloudrun_unallocated_paise)}
        </p>
      )}
      {data.telephony_unallocated_paise > 0 && (
        <p className="banner">
          {t.billTelUnallocated} {paiseToInput(data.telephony_unallocated_paise)}
        </p>
      )}
    </>
  );

  if (tab === "overview") {
    return (
      <>
        {banners}
        <div className="grid">
          <div className="stat">
            <span className="stat-value">{formatPaise(cloudTotal)}</span>
            <span className="stat-label">{t.billKpiCloud}</span>
          </div>
          <div className="stat">
            <span className="stat-value">{formatPaise(llmTotal)}</span>
            <span className="stat-label">{t.billKpiLlm}</span>
          </div>
          <div className="stat">
            <span className="stat-value">{formatPaise(telTotal)}</span>
            <span className="stat-label">{t.billKpiTelephony}</span>
          </div>
          <div className="stat">
            <span className="stat-value">{formatPaise(feeTotal)}</span>
            <span className="stat-label">{t.billKpiFee}</span>
          </div>
        </div>
        <section className="card">
          <h2>{t.billBreakdownTitle}</h2>
          <p className="empty" style={{ margin: "0 0 .5rem" }}>
            {t.billBreakdownSub}
          </p>
          <div className="breakrow">
            <span>{t.billKpiCloud}</span>
            <div className="bar">
              <div style={{ width: `${pct(cloudTotal)}%` }} />
            </div>
            <b>{formatPaise(cloudTotal)}</b>
            <span className="empty">{pct(cloudTotal)}%</span>
          </div>
          <div className="breakrow">
            <span>{t.billKpiLlm}</span>
            <div className="bar">
              <div style={{ width: `${pct(llmTotal)}%` }} />
            </div>
            <b>{formatPaise(llmTotal)}</b>
            <span className="empty">{pct(llmTotal)}%</span>
          </div>
          <div className="breakrow">
            <span>{t.billKpiTelephony}</span>
            <div className="bar">
              <div style={{ width: `${pct(telTotal)}%` }} />
            </div>
            <b>{formatPaise(telTotal)}</b>
            <span className="empty">{pct(telTotal)}%</span>
          </div>
          <div className="breakrow">
            <span>{t.billKpiFee}</span>
            <div className="bar">
              <div style={{ width: `${pct(feeTotal)}%` }} />
            </div>
            <b>{formatPaise(feeTotal)}</b>
            <span className="empty">{pct(feeTotal)}%</span>
          </div>
        </section>
        <section className="card">
          <h2>{t.billPeriodSettings}</h2>
          <Field label={t.billUsdInr} value={fx} onChange={setFx} placeholder="88" />
          <Field label={t.billCloudActual} value={actual} onChange={setActual} placeholder="0.00" />
          <small>{t.billCloudActualHint}</small>
          <Field label={t.billTelActual} value={telActual} onChange={setTelActual} placeholder="0.00" />
          <small>{t.billTelActualHint}</small>
          <div className="row">
            <button type="button" onClick={() => void savePeriod()} disabled={busy || !fxValid || !actualValid || !telActualValid}>
              {t.billSavePeriod}
            </button>
            <button type="button" className="secondary" onClick={() => void generate(null)} disabled={!canGenerate}>
              {t.billGenerateAll}
            </button>
          </div>
        </section>
        <section className="card">
          <h2>{t.billClientWiseTitle}</h2>
          <p className="empty" style={{ margin: "0 0 .5rem" }}>
            {t.billClientWiseSub}
          </p>
          {data.rows.length === 0 ? (
            <Empty>{t.noRows}</Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t.brandName}</th>
                    <th className="num">{t.billCloudCost}</th>
                    <th className="num">{t.billLlmCost}</th>
                    <th className="num">{t.billCallCost}</th>
                    <th className="num">{t.billDefaultFee}</th>
                    <th>{t.billInvoice}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((row) => (
                    <tr key={row.tenant_id}>
                      <td>
                        {row.name}
                        <br />
                        <small>{row.slug}</small>
                      </td>
                      <td className="num">{row.cloudrun_paise === null ? "-" : formatPaise(row.cloudrun_paise)}</td>
                      <td className="num">{row.llm_paise === null ? "-" : formatPaise(row.llm_paise)}</td>
                      <td className="num">{row.telephony_paise === null ? "-" : formatPaise(row.telephony_paise)}</td>
                      <td className="num">{formatPaise(row.default_platform_fee_paise)}</td>
                      <td>
                        {row.invoice ? (
                          <>
                            <Chip tone={row.invoice.status === "final" ? "ok" : "warn"}>{row.invoice.status === "final" ? t.billStatus_final : t.billStatus_draft}</Chip>
                            <br />
                            <small>{row.invoice.invoice_no ?? "-"}</small> {formatPaise(row.invoice.total_paise)}
                          </>
                        ) : (
                          "-"
                        )}
                      </td>
                      <td>
                        <div className="row">
                          {row.invoice?.status !== "final" && (
                            <button type="button" className="secondary" disabled={!canGenerate} onClick={() => void generate(row.tenant_id)} aria-label={`${t.billGenerateOne} ${row.name}`}>
                              {t.billGenerateOne}
                            </button>
                          )}
                          {row.invoice && (
                            <Link to={`/admin/billing/invoices/${row.invoice.id}`} aria-label={`${t.billOpenInvoice} ${row.name}`}>
                              {t.billOpenInvoice}
                            </Link>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </>
    );
  }

  if (tab === "invoices") {
    const withInvoice = data.rows.filter((row) => row.invoice !== null);
    const filtered = withInvoice.filter((row) => invoiceFilter === "all" || row.invoice?.status === invoiceFilter);
    return (
      <>
        {banners}
        <p className="banner">{t.billPaymentNote}</p>
        <section className="card">
          <h2>{t.billInvoicesTitle}</h2>
          <p className="empty" style={{ margin: "0 0 .5rem" }}>
            {t.billInvoicesSub}
          </p>
          <div className="subtabs">
            <button type="button" className={invoiceFilter === "all" ? "active" : ""} onClick={() => setInvoiceFilter("all")}>
              {t.billInvoiceFilterAll} ({withInvoice.length})
            </button>
            <button type="button" className={invoiceFilter === "draft" ? "active" : ""} onClick={() => setInvoiceFilter("draft")}>
              {t.billInvoiceFilterDraft} ({withInvoice.filter((r) => r.invoice?.status === "draft").length})
            </button>
            <button type="button" className={invoiceFilter === "final" ? "active" : ""} onClick={() => setInvoiceFilter("final")}>
              {t.billInvoiceFilterFinal} ({withInvoice.filter((r) => r.invoice?.status === "final").length})
            </button>
          </div>
          {filtered.length === 0 ? (
            <Empty>{t.noRows}</Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t.billInvNo}</th>
                    <th>{t.brandName}</th>
                    <th>{t.billForPeriod}</th>
                    <th className="num">{t.billAmount}</th>
                    <th>{t.billInvoice}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((row) => (
                    <tr key={row.tenant_id}>
                      <td>{row.invoice?.invoice_no ?? "-"}</td>
                      <td>
                        {row.name} <small>{row.slug}</small>
                      </td>
                      <td>{data.period}</td>
                      <td className="num">{formatPaise(row.invoice?.total_paise ?? 0)}</td>
                      <td>
                        <Chip tone={row.invoice?.status === "final" ? "ok" : "warn"}>{row.invoice?.status === "final" ? t.billStatus_final : t.billStatus_draft}</Chip>
                      </td>
                      <td>
                        <Link to={`/admin/billing/invoices/${row.invoice?.id}`} aria-label={`${t.billOpenInvoice} ${row.name}`}>
                          {t.billOpenInvoice}
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </>
    );
  }

  return (
    <>
      {banners}
      <section className="card">
        <h2>{t.billUsageTitle}</h2>
        <p className="empty" style={{ margin: "0 0 .5rem" }}>
          {t.billUsageSub}
        </p>
        {data.rows.length === 0 ? (
          <Empty>{t.noRows}</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t.brandName}</th>
                  <th>{t.billCalls}</th>
                  <th>{t.billTokens}</th>
                  <th>{t.billLlmCost}</th>
                  <th>{t.billCloudTime}</th>
                  <th>{t.billCloudCost}</th>
                  <th>{t.billCallTime}</th>
                  <th>{t.billCallCost}</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row) => (
                  <tr key={row.tenant_id}>
                    <td>
                      {row.name}
                      <br />
                      <small>{row.slug}</small>
                    </td>
                    <td className="num">{tokens(row.calls)}</td>
                    <td className="num">
                      {tokens(row.input_tokens)} / {tokens(row.output_tokens)}
                    </td>
                    <td className="num">{row.llm_paise === null ? "-" : formatPaise(row.llm_paise)}</td>
                    <td className="num">
                      {hoursText(row.compute_seconds)} h
                      <br />
                      <small>{row.jobs} jobs</small>
                    </td>
                    <td className="num">{row.cloudrun_paise === null ? "-" : formatPaise(row.cloudrun_paise)}</td>
                    <td className="num">
                      {minutesText(row.voice_call_seconds)} min
                      <br />
                      <small>{row.voice_calls} calls</small>
                    </td>
                    <td className="num">{row.telephony_paise === null ? "-" : formatPaise(row.telephony_paise)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <th>{t.billTotals}</th>
                  <th className="num">{tokens(sum((r) => r.calls))}</th>
                  <th className="num">
                    {tokens(sum((r) => r.input_tokens))} / {tokens(sum((r) => r.output_tokens))}
                  </th>
                  <th className="num">{formatPaise(llmTotal)}</th>
                  <th className="num">{hoursText(sum((r) => r.compute_seconds))} h</th>
                  <th className="num">{formatPaise(cloudTotal)}</th>
                  <th className="num">{minutesText(sum((r) => r.voice_call_seconds))} min</th>
                  <th className="num">{formatPaise(telTotal)}</th>
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

type SettingTab = "cloud" | "llm" | "speech" | "company" | "brands" | "plivo";
const SETTING_TABS: [SettingTab, Key][] = [
  ["cloud", "billSettingTabCloud"],
  ["llm", "billSettingTabLlm"],
  ["speech", "billSettingTabSpeech"],
  ["company", "billSettingTabCompany"],
  ["brands", "billSettingTabBrands"],
  ["plivo", "billSettingTabPlivo"],
];

export function AdminBillingSettings() {
  const [tab, setTab] = useState<SettingTab>("cloud");
  const [settings, reloadSettings] = useApi(() => api.get<BillingSettings>("/admin/billing/settings"));
  const [rates, reloadRates] = useApi(() => api.get<BillingRates>("/admin/billing/llm-rates"));
  const [speech, reloadSpeech] = useApi(() => api.get<SpeechRatesOut>("/admin/billing/speech-rates"));
  const [profiles, reloadProfiles] = useApi(() => api.get<BillingProfile[]>("/admin/billing/profiles"));
  const [fill, setFill] = useState<{ provider: string; model: string } | null>(null);
  return (
    <Screen title={t.billSettingsTitle}>
      <Link to="/admin/billing">{t.billBack}</Link>
      <div className="tabs" role="tablist">
        {SETTING_TABS.map(([key, label]) => (
          <button key={key} type="button" role="tab" className={tab === key ? "active" : ""} onClick={() => setTab(key)}>
            {t[label]}
          </button>
        ))}
      </div>
      {tab === "cloud" && (
        <Async value={settings} reload={reloadSettings}>
          {(current) => <CloudRunForm key={current.updated_at ?? "new"} current={current} onSaved={reloadSettings} />}
        </Async>
      )}
      {tab === "llm" && (
        <Async value={rates} reload={reloadRates}>
          {(current) => <RatesPanel current={current} fill={fill} onFill={setFill} onSaved={() => { setFill(null); reloadRates(); }} />}
        </Async>
      )}
      {tab === "speech" && (
        <Async value={speech} reload={reloadSpeech}>
          {(current) => <SpeechRatesPanel current={current} onSaved={reloadSpeech} />}
        </Async>
      )}
      {tab === "company" && (
        <Async value={settings} reload={reloadSettings}>
          {(current) => <CompanyForm key={current.updated_at ?? "new"} current={current} onSaved={reloadSettings} />}
        </Async>
      )}
      {tab === "brands" && (
        <Async value={profiles} reload={reloadProfiles}>
          {(list) =>
            list.length === 0 ? (
              <Empty>{t.noRows}</Empty>
            ) : (
              <>
                {list.map((profile) => (
                  <ProfileForm key={`${profile.tenant_id}-${profile.legal_name}-${profile.state_code}-${profile.default_platform_fee_paise}`} profile={profile} onSaved={reloadProfiles} />
                ))}
              </>
            )
          }
        </Async>
      )}
      {tab === "plivo" && <PlivoPanel />}
    </Screen>
  );
}

function useSettingsForm(current: BillingSettings) {
  const toast = useToast();
  const [form, setForm] = useState(current);
  const [busy, setBusy] = useState(false);
  const set = <K extends keyof BillingSettings>(key: K, value: BillingSettings[K]) => setForm({ ...form, [key]: value });

  const save = async (onSaved: () => void) => {
    setBusy(true);
    try {
      const { updated_at: _ignored, ...body } = form;
      void _ignored;
      await api.put<BillingSettings>("/admin/billing/settings", body);
      toast.info(t.billSettingsSaved);
      onSaved();
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(false);
    }
  };

  return { form, set, busy, save };
}

function CompanyForm({ current, onSaved }: { current: BillingSettings; onSaved: () => void }) {
  const { form, set, busy, save } = useSettingsForm(current);
  return (
    <>
      <section className="card">
        <h2>{t.billSellerTitle}</h2>
        <Field label={t.billSellerName} value={form.seller_name} onChange={(v) => set("seller_name", v)} />
        <Field label={t.billSellerAddress} value={form.seller_address} onChange={(v) => set("seller_address", v)} />
        <StateSelect label={t.billSellerState} value={form.seller_state_code} onChange={(v) => set("seller_state_code", v)} />
        <Field label={t.billSellerGstin} value={form.seller_gstin} onChange={(v) => set("seller_gstin", v.toUpperCase())} />
        <Field label={t.billSellerEmail} value={form.seller_email} onChange={(v) => set("seller_email", v)} />
        <Field label={t.billSellerPhone} value={form.seller_phone} onChange={(v) => set("seller_phone", v)} />
        <Field label={t.billPayment} value={form.payment_details} onChange={(v) => set("payment_details", v)} />
        <Field label={t.billSac} value={form.sac_code} onChange={(v) => set("sac_code", v)} />
        <Field label={t.billPrefix} value={form.invoice_prefix} onChange={(v) => set("invoice_prefix", v.toUpperCase())} />
      </section>
      <section className="card">
        <h2>{t.billGstTitle}</h2>
        <label className="field">
          <span>{t.billGstEnabled}</span>
          <input type="checkbox" aria-label={t.billGstEnabled} checked={form.gst_enabled} onChange={(event) => set("gst_enabled", event.target.checked)} />
        </label>
        <Field label={t.billGstRate} value={form.gst_rate_percent} onChange={(v) => set("gst_rate_percent", v)} />
        {form.gst_enabled && form.seller_gstin === "" && <p className="banner">{t.billGstNoGstin}</p>}
        <button type="button" onClick={() => void save(onSaved)} disabled={busy}>
          {t.save}
        </button>
      </section>
    </>
  );
}

function CloudRunForm({ current, onSaved }: { current: BillingSettings; onSaved: () => void }) {
  const { form, set, busy, save } = useSettingsForm(current);
  return (
    <section className="card">
      <h2>{t.billCloudParams}</h2>
      <Field label={t.billVcpu} value={form.cloudrun_vcpu} onChange={(v) => set("cloudrun_vcpu", v)} />
      <Field label={t.billGib} value={form.cloudrun_gib} onChange={(v) => set("cloudrun_gib", v)} />
      <Field label={t.billUsdVcpu} value={form.cloudrun_usd_per_vcpu_second} onChange={(v) => set("cloudrun_usd_per_vcpu_second", v)} />
      <Field label={t.billUsdGib} value={form.cloudrun_usd_per_gib_second} onChange={(v) => set("cloudrun_usd_per_gib_second", v)} />
      <small>{t.billCloudActualHint}</small>
      <div className="row" style={{ marginTop: ".5rem" }}>
        <button type="button" onClick={() => void save(onSaved)} disabled={busy}>
          {t.save}
        </button>
      </div>
    </section>
  );
}

const RATE_PROVIDERS = ["anthropic", "openai", "groq", "openrouter", "openai_compatible"];

function RatesPanel({
  current,
  fill,
  onFill,
  onSaved,
}: {
  current: BillingRates;
  fill: { provider: string; model: string } | null;
  onFill: (value: { provider: string; model: string } | null) => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [provider, setProvider] = useState(fill?.provider ?? "anthropic");
  const [model, setModel] = useState(fill?.model ?? "");
  const [input, setInput] = useState("");
  const [output, setOutput] = useState("");
  const valid = model.trim() !== "" && /^\d+(\.\d{1,6})?$/.test(input.trim()) && /^\d+(\.\d{1,6})?$/.test(output.trim());

  const edit = (rate: { provider: string; model: string; input_usd_per_million?: string; output_usd_per_million?: string }) => {
    setProvider(rate.provider);
    setModel(rate.model);
    setInput(rate.input_usd_per_million ? String(Number(rate.input_usd_per_million)) : "");
    setOutput(rate.output_usd_per_million ? String(Number(rate.output_usd_per_million)) : "");
    onFill({ provider: rate.provider, model: rate.model });
  };

  const save = async () => {
    try {
      await api.put<BillingLlmRate>("/admin/billing/llm-rates", {
        provider,
        model: model.trim(),
        input_usd_per_million: input.trim(),
        output_usd_per_million: output.trim(),
      });
      toast.info(t.billRateSaved);
      setModel("");
      setInput("");
      setOutput("");
      onSaved();
    } catch (err) {
      toast.error(err);
    }
  };

  return (
    <section className="card">
      <h2>{t.billRatesTitle}</h2>
      {current.unpriced.length > 0 && (
        <p className="banner">
          {t.billUnpriced}
          {current.unpriced.map((item) => (
            <button key={`${item.provider}/${item.model}`} type="button" className="secondary" onClick={() => edit(item)}>
              {item.provider}/{item.model}: {t.billRateFill}
            </button>
          ))}
        </p>
      )}
      {current.rates.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t.billRateProvider}</th>
                <th>{t.billRateModel}</th>
                <th>{t.billRateIn}</th>
                <th>{t.billRateOut}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {current.rates.map((rate) => (
                <tr key={`${rate.provider}/${rate.model}`}>
                  <td>{rate.provider}</td>
                  <td>{rate.model}</td>
                  <td className="num">{rate.input_usd_per_million}</td>
                  <td className="num">{rate.output_usd_per_million}</td>
                  <td>
                    <button type="button" className="secondary" onClick={() => edit(rate)} aria-label={`${t.billRateFill} ${rate.model}`}>
                      {t.billRateFill}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <label className="field">
        <span>{t.billRateProvider}</span>
        <select aria-label={t.billRateProvider} value={provider} onChange={(event) => setProvider(event.target.value)}>
          {RATE_PROVIDERS.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </label>
      <Field label={t.billRateModel} value={model} onChange={setModel} />
      <Field label={t.billRateIn} value={input} onChange={setInput} />
      <Field label={t.billRateOut} value={output} onChange={setOutput} />
      <button type="button" onClick={() => void save()} disabled={!valid}>
        {t.billRateSave}
      </button>
    </section>
  );
}

const SPEECH_PROVIDERS: SpeechProvider[] = ["sarvam_stt", "google_stt", "sarvam_tts", "google_tts"];

function SpeechRatesPanel({ current, onSaved }: { current: SpeechRatesOut; onSaved: () => void }) {
  const byProvider = new Map(current.rates.map((r) => [r.provider, r]));
  return (
    <section className="card">
      <h2>{t.billSpeechTitle}</h2>
      <p className="banner">{t.billSpeechNote}</p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{t.billSpeechProvider}</th>
              <th>{t.billSpeechUnit}</th>
              <th className="num">{t.billSpeechRate}</th>
              <th>{t.billSpeechActive}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {SPEECH_PROVIDERS.map((provider) => (
              <SpeechRateRow key={provider} provider={provider} existing={byProvider.get(provider) ?? null} onSaved={onSaved} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function SpeechRateRow({ provider, existing, onSaved }: { provider: SpeechProvider; existing: SpeechRate | null; onSaved: () => void }) {
  const toast = useToast();
  const [unit, setUnit] = useState(existing?.unit ?? "");
  const [rate, setRate] = useState(existing ? String(Number(existing.rate_inr)) : "");
  const [active, setActive] = useState(existing?.active ?? true);
  const valid = unit.trim() !== "" && /^\d+(\.\d{1,6})?$/.test(rate.trim());

  const save = async () => {
    try {
      await api.put<SpeechRate>("/admin/billing/speech-rates", {
        provider,
        unit: unit.trim(),
        rate_inr: rate.trim(),
        active,
      });
      toast.info(t.billSpeechSaved);
      onSaved();
    } catch (err) {
      toast.error(err);
    }
  };

  return (
    <tr>
      <td>{t[`billSpeech_${provider}` as Key]}</td>
      <td>
        <input aria-label={`${t.billSpeechUnit} ${provider}`} value={unit} onChange={(e) => setUnit(e.target.value)} placeholder="per_minute" />
      </td>
      <td className="num">
        <input aria-label={`${t.billSpeechRate} ${provider}`} value={rate} onChange={(e) => setRate(e.target.value)} placeholder="0.00" />
      </td>
      <td>
        <input type="checkbox" aria-label={`${t.billSpeechActive} ${provider}`} checked={active} onChange={(e) => setActive(e.target.checked)} />
      </td>
      <td>
        <button type="button" className="secondary" onClick={() => void save()} disabled={!valid}>
          {t.billSpeechSave}
        </button>
      </td>
    </tr>
  );
}

function PlivoPanel() {
  return (
    <section className="card">
      <h2>{t.billPlivoTitle}</h2>
      <p className="banner">{t.billPlivoNote}</p>
      <Link to="/admin/billing">{t.billPlivoGoOverview}</Link>
    </section>
  );
}

function ProfileForm({ profile, onSaved }: { profile: BillingProfile; onSaved: () => void }) {
  const toast = useToast();
  const [form, setForm] = useState({
    legal_name: profile.legal_name,
    address: profile.address,
    state_code: profile.state_code,
    gstin: profile.gstin,
    email: profile.email,
    fee: paiseToInput(profile.default_platform_fee_paise),
  });
  const fee = rupeesToPaise(form.fee);
  const set = (key: keyof typeof form) => (value: string) => setForm({ ...form, [key]: value });

  const save = async () => {
    if (fee === null) return;
    try {
      await api.put(`/admin/billing/profiles/${profile.tenant_id}`, {
        legal_name: form.legal_name,
        address: form.address,
        state_code: form.state_code,
        gstin: form.gstin,
        email: form.email,
        default_platform_fee_paise: fee,
      });
      toast.info(t.billProfileSaved);
      onSaved();
    } catch (err) {
      toast.error(err);
    }
  };

  return (
    <section className="card" aria-label={profile.tenant_name}>
      <h2>
        {profile.tenant_name} <small>{profile.slug}</small>
      </h2>
      <Field label={`${t.billLegalName} (${profile.slug})`} value={form.legal_name} onChange={set("legal_name")} />
      <Field label={`${t.billBuyerAddress} (${profile.slug})`} value={form.address} onChange={set("address")} />
      <StateSelect label={`${t.billBuyerState} (${profile.slug})`} value={form.state_code} onChange={set("state_code")} />
      <Field label={`${t.billBuyerGstin} (${profile.slug})`} value={form.gstin} onChange={(v) => set("gstin")(v.toUpperCase())} />
      <Field label={`${t.billBuyerEmail} (${profile.slug})`} value={form.email} onChange={set("email")} />
      <Field label={`${t.billFeeMonthly} (${profile.slug})`} value={form.fee} onChange={set("fee")} />
      <button type="button" onClick={() => void save()} disabled={fee === null} aria-label={`${t.save} ${profile.slug}`}>
        {t.save}
      </button>
    </section>
  );
}

export function AdminInvoice() {
  const { id } = useParams();
  const [state, reload] = useApi(() => api.get<Invoice>(`/admin/billing/invoices/${id}`), [id]);
  return (
    <Async value={state} reload={reload}>
      {(invoice) => <InvoiceView key={`${invoice.id}-${invoice.status}-${invoice.total_paise}-${invoice.generated_at}`} invoice={invoice} reload={reload} />}
    </Async>
  );
}

function lineQuantity(line: InvoiceLine): string {
  return String(Number(line.quantity));
}

function lineNote(line: InvoiceLine, invoice: Invoice): string | null {
  if (line.kind === "llm") {
    return `${tokens(Number(line.detail.input_tokens ?? 0))} / ${tokens(Number(line.detail.output_tokens ?? 0))} tokens`;
  }
  if (line.kind === "cloudrun") {
    return invoice.cloudrun_basis === "actual" ? t.billBasisActual : t.billBasisEstimate;
  }
  if (line.kind === "telephony") {
    return `${tokens(Number(line.detail.calls ?? 0))} calls`;
  }
  return null;
}

function InvoiceView({ invoice, reload }: { invoice: Invoice; reload: () => void }) {
  const toast = useToast();
  const draft = invoice.status === "draft";
  const [fee, setFee] = useState(paiseToInput(invoice.platform_fee_paise));
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const feePaise = rupeesToPaise(fee);
  const half = Number(invoice.gst_rate_percent) / 2;

  const run = async (action: () => Promise<unknown>, message: string) => {
    setBusy(true);
    try {
      await action();
      toast.info(message);
      reload();
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(false);
      setAsking(false);
    }
  };

  const saveFee = () => run(() => api.put(`/admin/billing/invoices/${invoice.id}/fee`, { platform_fee_paise: feePaise }), t.billFeeSaved);
  const refresh = () => run(() => api.post("/admin/billing/invoices/generate", { period: invoice.period, tenant_id: invoice.tenant_id }), t.billRefreshed);
  const finalize = () => run(() => api.post(`/admin/billing/invoices/${invoice.id}/finalize`, { expected_total_paise: invoice.total_paise }), t.billFinalized);

  return (
    <section className="screen">
      <div className="no-print">
        <Link to="/admin/billing">{t.billBack}</Link>
        <h1>
          {invoice.tenant_name} - {invoice.period}{" "}
          <Chip tone={draft ? "warn" : "ok"}>{draft ? t.billStatus_draft : t.billStatus_final}</Chip>
        </h1>
        {invoice.blockers.length > 0 && (
          <div className="banner" role="alert">
            <strong>{t.billBlockers}</strong>
            <ul>
              {invoice.blockers.map((flag) => (
                <li key={`${flag.code}-${flag.detail ?? ""}`}>{flagText(flag)}</li>
              ))}
            </ul>
          </div>
        )}
        {invoice.warnings.length > 0 && (
          <div className="banner">
            <strong>{t.billWarnings}</strong>
            <ul>
              {invoice.warnings.map((flag) => (
                <li key={`${flag.code}-${flag.detail ?? ""}`}>{flagText(flag)}</li>
              ))}
            </ul>
          </div>
        )}
        {draft && (
          <section className="card">
            <Field label={t.billFee} value={fee} onChange={setFee} placeholder="0.00" />
            <div className="row">
              <button type="button" className="secondary" onClick={() => void saveFee()} disabled={busy || feePaise === null}>
                {t.billFeeSave}
              </button>
              <button type="button" className="secondary" onClick={() => void refresh()} disabled={busy}>
                {t.billRefresh}
              </button>
              <button type="button" onClick={() => setAsking(true)} disabled={busy || invoice.blockers.length > 0}>
                {t.billFinalize}
              </button>
            </div>
          </section>
        )}
        <div className="row">
          <button type="button" className="secondary" onClick={() => window.print()}>
            {t.billPrint}
          </button>
        </div>
      </div>

      <article className={draft ? "invoice invoice-draft" : "invoice"} aria-label="invoice">
        <header className="invoice-head">
          <div>
            <h1>{invoice.doc_type === "tax_invoice" ? t.billDocTax : t.billDocProforma}</h1>
            {draft && <strong>{t.billDraftMark}</strong>}
          </div>
          <div className="invoice-meta">
            <div>
              {t.billInvNo}: {invoice.invoice_no ?? "-"}
            </div>
            <div>
              {t.billInvDate}: {invoice.invoice_date ?? "-"}
            </div>
            <div>
              {t.billForPeriod}: {invoice.period}
            </div>
          </div>
        </header>
        <div className="invoice-parties">
          <div>
            <strong>{t.billFrom}</strong>
            <div>{invoice.seller.name}</div>
            <div className="pre">{invoice.seller.address}</div>
            <div>{stateName(invoice.seller.state_code)}</div>
            {invoice.seller.gstin && <div>GSTIN: {invoice.seller.gstin}</div>}
            {invoice.seller.email && <div>{invoice.seller.email}</div>}
            {invoice.seller.phone && <div>{invoice.seller.phone}</div>}
          </div>
          <div>
            <strong>{t.billBillTo}</strong>
            <div>{invoice.buyer.name}</div>
            <div className="pre">{invoice.buyer.address}</div>
            <div>{stateName(invoice.buyer.state_code)}</div>
            {invoice.buyer.gstin && <div>GSTIN: {invoice.buyer.gstin}</div>}
            {invoice.buyer.email && <div>{invoice.buyer.email}</div>}
          </div>
        </div>
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>{t.billDesc}</th>
              <th className="num">{t.billQty}</th>
              <th className="num">{t.billAmount}</th>
            </tr>
          </thead>
          <tbody>
            {invoice.lines.map((line) => (
              <tr key={line.position}>
                <td>{line.position}</td>
                <td>
                  {line.description}
                  {lineNote(line, invoice) && (
                    <>
                      <br />
                      <small>{lineNote(line, invoice)}</small>
                    </>
                  )}
                </td>
                <td className="num">
                  {lineQuantity(line)} {line.unit}
                </td>
                <td className="num">{formatPaise(line.amount_paise)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td colSpan={3} className="num">
                {t.billSubtotal}
              </td>
              <td className="num">{formatPaise(invoice.subtotal_paise)}</td>
            </tr>
            {invoice.gst_kind === "intra" && (
              <>
                <tr>
                  <td colSpan={3} className="num">
                    {t.billCgst} {half}%
                  </td>
                  <td className="num">{formatPaise(invoice.cgst_paise)}</td>
                </tr>
                <tr>
                  <td colSpan={3} className="num">
                    {t.billSgst} {half}%
                  </td>
                  <td className="num">{formatPaise(invoice.sgst_paise)}</td>
                </tr>
              </>
            )}
            {invoice.gst_kind === "inter" && (
              <tr>
                <td colSpan={3} className="num">
                  {t.billIgst} {Number(invoice.gst_rate_percent)}%
                </td>
                <td className="num">{formatPaise(invoice.igst_paise)}</td>
              </tr>
            )}
            <tr>
              <th colSpan={3} className="num">
                {t.billGrandTotal}
              </th>
              <th className="num">{formatPaise(invoice.total_paise)}</th>
            </tr>
          </tfoot>
        </table>
        <footer className="invoice-foot">
          {invoice.seller.sac_code && (
            <p>
              {t.billSacLabel}: {invoice.seller.sac_code}
            </p>
          )}
          <p>USD/INR: {Number(invoice.usd_inr)}</p>
          {invoice.seller.payment_details && (
            <p className="pre">
              <strong>{t.billPayTo}</strong>
              {"\n"}
              {invoice.seller.payment_details}
            </p>
          )}
          {invoice.doc_type === "proforma" && <p>{t.billProformaNote}</p>}
        </footer>
      </article>

      {asking && (
        <div className="dialog no-print" role="dialog" aria-modal="true" aria-label={t.billFinalize}>
          <div className="dialog-box">
            <h2>{t.billFinalize}</h2>
            <p>{t.billFinalizeAsk}</p>
            <div className="row">
              <button type="button" onClick={() => void finalize()} disabled={busy}>
                {t.confirm}
              </button>
              <button type="button" className="secondary" onClick={() => setAsking(false)}>
                {t.cancel}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
