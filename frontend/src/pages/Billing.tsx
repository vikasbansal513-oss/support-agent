import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, downloadFile } from "../api/client";
import type {
  BillingLlmRate,
  BillingPeriod,
  BillingPeriodRow,
  BillingProfile,
  BillingRates,
  BillingSettings,
  CostCategoriesOut,
  CostCategory,
  GcpReconciliation,
  GcpUnallocated,
  GcpUnallocatedListOut,
  GcpUsageEvent,
  GcpUsageEventsOut,
  GenerateResult,
  Invoice,
  InvoiceLine,
  PaymentStatus,
  PlivoCall,
  PlivoCallsOut,
  PlivoNumberAssignment,
  SpeechProvider,
  SpeechRate,
  SpeechRatesOut,
  SpeechUsageListOut,
  SpeechUsageRecord,
} from "../api/types";
import {
  GST_STATES,
  flagText,
  isPositiveDecimal,
  paiseToInput,
  paymentStatusTone,
  previousMonthIst,
  stateName,
  tokens,
} from "../billing";
import { Async, Chip, Empty, Field, Screen, StatCard } from "../components/common";
import { Icon } from "../components/Icons";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { formatPaise, rupeesToPaise } from "../format";
import { t, type Key } from "../i18n";
import { BillingIntel } from "./BillingIntel";

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

type OverviewTab = "overview" | "invoices" | "usage" | "intel";

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
        <Link to="/admin/billing/settings" className="btn">
          <Icon name="settings" className="sm" /> {t.billSettingsLink}
        </Link>
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
        <button type="button" role="tab" className={tab === "intel" ? "active" : ""} onClick={() => setTab("intel")}>
          {t.billTabIntel}
        </button>
      </div>
      {tab === "intel" ? (
        <BillingIntel />
      ) : (
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
      )}
    </Screen>
  );
}

function PeriodView({ data, reload, tab }: { data: BillingPeriod; reload: () => void; tab: OverviewTab }) {
  const toast = useToast();
  const [fx, setFx] = useState(data.usd_inr ? String(Number(data.usd_inr)) : "");
  const [actual, setActual] = useState(data.cloudrun_actual_paise === null ? "" : paiseToInput(data.cloudrun_actual_paise));
  const [telActual, setTelActual] = useState(data.telephony_actual_paise === null ? "" : paiseToInput(data.telephony_actual_paise));
  const [busy, setBusy] = useState(false);
  const [invoiceFilter, setInvoiceFilter] = useState<"all" | PaymentStatus>("all");
  const [search, setSearch] = useState("");

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

  const setPaid = async (invoiceId: string, paid: boolean) => {
    setBusy(true);
    try {
      await api.put(`/admin/billing/invoices/${invoiceId}/paid`, { paid });
      toast.info(paid ? t.billMarkPaidDone : t.billMarkUnpaidDone);
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
        <div className="stats">
          <StatCard label={t.billKpiCloud} value={formatPaise(cloudTotal)} icon="file" />
          <StatCard label={t.billKpiLlm} value={formatPaise(llmTotal)} icon="bot" />
          <StatCard label={t.billKpiTelephony} value={formatPaise(telTotal)} icon="phone" />
          <StatCard label={t.billKpiFee} value={formatPaise(feeTotal)} icon="info" />
        </div>
        <section className="card pad">
          <div className="card-head">
            <h2 className="card-title">{t.billBreakdownTitle}</h2>
          </div>
          <p className="card-sub" style={{ margin: "0 0 .8rem" }}>
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
        <section className="card pad">
          <div className="card-head">
            <h2 className="card-title">{t.billPeriodSettings}</h2>
          </div>
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
        <section className="card pad">
          <div className="card-head">
            <h2 className="card-title">{t.billClientWiseTitle}</h2>
          </div>
          <p className="card-sub" style={{ margin: "0 0 .8rem" }}>
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
                            <Chip tone={paymentStatusTone(row.invoice.payment_status)}>{t[`billPayStatus_${row.invoice.payment_status}` as Key]}</Chip>
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
    const bySearch = withInvoice.filter((row) => {
      const q = search.trim().toLowerCase();
      if (q === "") return true;
      return row.name.toLowerCase().includes(q) || (row.invoice?.invoice_no ?? "").toLowerCase().includes(q);
    });
    const filtered = bySearch.filter((row) => invoiceFilter === "all" || row.invoice?.payment_status === invoiceFilter);
    const countOf = (status: PaymentStatus) => withInvoice.filter((r) => r.invoice?.payment_status === status).length;
    return (
      <>
        {banners}
        <p className="banner">{t.billPaymentNote}</p>
        <section className="card pad">
          <div className="card-head">
            <h2 className="card-title">{t.billInvoicesTitle}</h2>
          </div>
          <p className="card-sub" style={{ margin: "0 0 .8rem" }}>
            {t.billInvoicesSub}
          </p>
          <div className="filters">
            <button type="button" className={invoiceFilter === "all" ? "filter-tab active" : "filter-tab"} onClick={() => setInvoiceFilter("all")}>
              {t.billInvoiceFilterAll} ({withInvoice.length})
            </button>
            <button type="button" className={invoiceFilter === "draft" ? "filter-tab active" : "filter-tab"} onClick={() => setInvoiceFilter("draft")}>
              {t.billInvoiceFilterDraft} ({countOf("draft")})
            </button>
            <button type="button" className={invoiceFilter === "paid" ? "filter-tab active" : "filter-tab"} onClick={() => setInvoiceFilter("paid")}>
              {t.billInvoiceFilterPaid} ({countOf("paid")})
            </button>
            <button type="button" className={invoiceFilter === "unpaid" ? "filter-tab active" : "filter-tab"} onClick={() => setInvoiceFilter("unpaid")}>
              {t.billInvoiceFilterUnpaid} ({countOf("unpaid")})
            </button>
            <button type="button" className={invoiceFilter === "overdue" ? "filter-tab active" : "filter-tab"} onClick={() => setInvoiceFilter("overdue")}>
              {t.billInvoiceFilterOverdue} ({countOf("overdue")})
            </button>
            <button type="button" className={invoiceFilter === "partially_paid" ? "filter-tab active" : "filter-tab"} onClick={() => setInvoiceFilter("partially_paid")}>
              {t.billInvoiceFilterPartial} ({countOf("partially_paid")})
            </button>
            <button type="button" className={invoiceFilter === "cancelled" ? "filter-tab active" : "filter-tab"} onClick={() => setInvoiceFilter("cancelled")}>
              {t.billInvoiceFilterCancelled} ({countOf("cancelled")})
            </button>
            <div className="filter-right">
              <input className="search" aria-label={t.billSearchPlaceholder} placeholder={t.billSearchPlaceholder} value={search} onChange={(event) => setSearch(event.target.value)} />
            </div>
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
                    <th>{t.billDueDate}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((row) => {
                    const inv = row.invoice;
                    return (
                    <tr key={row.tenant_id}>
                      <td>{inv?.invoice_no ?? "-"}</td>
                      <td>
                        {row.name} <small>{row.slug}</small>
                      </td>
                      <td>{data.period}</td>
                      <td className="num">{formatPaise(inv?.total_paise ?? 0)}</td>
                      <td>
                        <Chip tone={paymentStatusTone(inv?.payment_status ?? "draft")}>{t[`billPayStatus_${inv?.payment_status ?? "draft"}` as Key]}</Chip>
                      </td>
                      <td>{inv?.due_date ?? "-"}</td>
                      <td>
                        <div className="row">
                          <Link to={`/admin/billing/invoices/${inv?.id}`} aria-label={`${t.billOpenInvoice} ${row.name}`}>
                            {t.billOpenInvoice}
                          </Link>
                          {inv && inv.status === "final" && (
                            <button type="button" className="secondary" onClick={() => void downloadFile(`/admin/billing/invoices/${inv.id}/pdf`, `${inv.invoice_no ?? "invoice"}.pdf`)}>
                              {t.billDownloadPdf}
                            </button>
                          )}
                          {inv && inv.status === "final" && inv.payment_status !== "paid" && inv.payment_status !== "cancelled" && (
                            <button type="button" className="secondary" disabled={busy} onClick={() => void setPaid(inv.id, true)} aria-label={`${t.billMarkPaid} ${row.name}`}>
                              {t.billMarkPaid}
                            </button>
                          )}
                          {inv && inv.status === "final" && inv.payment_status === "paid" && (
                            <button type="button" className="secondary" disabled={busy} onClick={() => void setPaid(inv.id, false)} aria-label={`${t.billMarkUnpaid} ${row.name}`}>
                              {t.billMarkUnpaid}
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                    );
                  })}
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
      <section className="card pad">
        <div className="card-head">
          <h2 className="card-title">{t.billUsageTitle}</h2>
        </div>
        <p className="card-sub" style={{ margin: "0 0 .8rem" }}>
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
                  <th>{t.billTelephonyBasis}</th>
                  <th className="num">{t.billNumberRental}</th>
                  <th className="num">{t.billSpeechCost}</th>
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
                    <td>
                      <small>{t[`billTelephonyBasis_${row.telephony_basis}` as Key]}</small>
                    </td>
                    <td className="num">{formatPaise(row.number_rental_paise)}</td>
                    <td className="num">{formatPaise(row.speech_paise)}</td>
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
                  <th />
                  <th className="num">{formatPaise(sum((r) => r.number_rental_paise))}</th>
                  <th className="num">{formatPaise(sum((r) => r.speech_paise))}</th>
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </section>
      <PlivoCallsPanel period={data.period} />
      <SpeechUsagePanel period={data.period} />
      <GcpUsageEventsPanel period={data.period} />
    </>
  );
}

function PlivoCallsPanel({ period }: { period: string }) {
  const [state, reload] = useApi(() => api.get<PlivoCallsOut>(`/admin/billing/plivo/calls/${period}`), [period]);
  return (
    <section className="card">
      <h2>{t.billPlivoCallsTitle}</h2>
      <Async value={state} reload={reload}>
        {(data) =>
          data.calls.length === 0 ? (
            <Empty>{t.billPlivoCallsEmpty}</Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Call UUID</th>
                    <th>Direction</th>
                    <th>From</th>
                    <th>To</th>
                    <th className="num">Billed (s)</th>
                    <th>Status</th>
                    <th className="num">{t.billAmount}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.calls.map((call: PlivoCall) => (
                    <tr key={call.id}>
                      <td>
                        <small>{call.call_uuid}</small>
                      </td>
                      <td>{call.direction}</td>
                      <td>{call.from_number}</td>
                      <td>{call.to_number}</td>
                      <td className="num">{call.billed_seconds}</td>
                      <td>{call.call_status}</td>
                      <td className="num">{formatPaise(call.computed_cost_paise)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Async>
    </section>
  );
}

function SpeechUsagePanel({ period }: { period: string }) {
  const toast = useToast();
  const [state, reload] = useApi(() => api.get<SpeechUsageListOut>(`/admin/billing/speech-usage/${period}`), [period]);
  const [profiles] = useApi(() => api.get<BillingProfile[]>("/admin/billing/profiles"));
  const [tenantId, setTenantId] = useState("");
  const [provider, setProvider] = useState<SpeechProvider>("sarvam_stt");
  const [qty, setQty] = useState("");
  const valid = tenantId !== "" && /^\d+(\.\d{1,4})?$/.test(qty.trim());

  const add = async () => {
    try {
      await api.post("/admin/billing/speech-usage", { tenant_id: tenantId, provider, quantity: qty.trim() });
      toast.info(t.billGcpUsageSaved);
      setQty("");
      reload();
    } catch (err) {
      toast.error(err);
    }
  };

  return (
    <section className="card">
      <h2>{t.billSpeechTitle} - {t.billUsageTitle}</h2>
      <div className="row">
        <select aria-label={t.billGcpUsageTenant} value={tenantId} onChange={(e) => setTenantId(e.target.value)}>
          <option value="">-</option>
          {(profiles.state === "data" ? profiles.data : []).map((p) => (
            <option key={p.tenant_id} value={p.tenant_id}>
              {p.tenant_name}
            </option>
          ))}
        </select>
        <select aria-label={t.billSpeechProvider} value={provider} onChange={(e) => setProvider(e.target.value as SpeechProvider)}>
          {SPEECH_PROVIDERS.map((p) => (
            <option key={p} value={p}>
              {t[`billSpeech_${p}` as Key]}
            </option>
          ))}
        </select>
        <input aria-label="Qty" placeholder="Qty" value={qty} onChange={(e) => setQty(e.target.value)} />
        <button type="button" className="secondary" onClick={() => void add()} disabled={!valid}>
          {t.billGcpUsageSave}
        </button>
      </div>
      <Async value={state} reload={reload}>
        {(data) =>
          data.records.length === 0 ? (
            <Empty>{t.noRows}</Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t.billSpeechProvider}</th>
                    <th className="num">Qty</th>
                    <th>{t.billSpeechUnit}</th>
                    <th className="num">{t.billAmount}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.records.map((row: SpeechUsageRecord) => (
                    <tr key={row.id}>
                      <td>{t[`billSpeech_${row.provider}` as Key]}</td>
                      <td className="num">{Number(row.quantity)}</td>
                      <td>{row.unit}</td>
                      <td className="num">{formatPaise(row.cost_paise)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Async>
    </section>
  );
}

function GcpUsageEventsPanel({ period }: { period: string }) {
  const toast = useToast();
  const [state, reload] = useApi(() => api.get<GcpUsageEventsOut>(`/admin/billing/gcp/usage-events/${period}`), [period]);
  const [categories] = useApi(() => api.get<CostCategoriesOut>("/admin/billing/cost-categories"));
  const [profiles] = useApi(() => api.get<BillingProfile[]>("/admin/billing/profiles"));
  const [tenantId, setTenantId] = useState("");
  const [category, setCategory] = useState("");
  const [qty, setQty] = useState("");
  const valid = category !== "" && /^\d+(\.\d{1,6})?$/.test(qty.trim());

  const add = async () => {
    try {
      await api.post("/admin/billing/gcp/usage-events", {
        tenant_id: tenantId === "" ? null : tenantId,
        category,
        quantity: qty.trim(),
      });
      toast.info(t.billGcpUsageSaved);
      setQty("");
      reload();
    } catch (err) {
      toast.error(err);
    }
  };

  return (
    <section className="card">
      <h2>{t.billGcpUsageTitle}</h2>
      <p className="empty" style={{ margin: "0 0 .5rem" }}>
        {t.billGcpUsageSub}
      </p>
      <div className="row">
        <select aria-label={t.billGcpUsageTenant} value={tenantId} onChange={(e) => setTenantId(e.target.value)}>
          <option value="">{t.billGcpUsageTenant}</option>
          {(profiles.state === "data" ? profiles.data : []).map((p) => (
            <option key={p.tenant_id} value={p.tenant_id}>
              {p.tenant_name}
            </option>
          ))}
        </select>
        <select aria-label={t.billGcpUsageCategory} value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">-</option>
          {(categories.state === "data" ? categories.data.categories : []).map((c) => (
            <option key={c.category} value={c.category}>
              {c.category}
            </option>
          ))}
        </select>
        <input aria-label={t.billGcpUsageQty} placeholder="Qty" value={qty} onChange={(e) => setQty(e.target.value)} />
        <button type="button" className="secondary" onClick={() => void add()} disabled={!valid}>
          {t.billGcpUsageSave}
        </button>
      </div>
      <Async value={state} reload={reload}>
        {(data) =>
          data.events.length === 0 ? (
            <Empty>{t.noRows}</Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t.billGcpUsageCategory}</th>
                    <th className="num">Qty</th>
                    <th>Unit</th>
                    <th className="num">Cost (USD)</th>
                  </tr>
                </thead>
                <tbody>
                  {data.events.map((row: GcpUsageEvent) => (
                    <tr key={row.id}>
                      <td>{row.category}</td>
                      <td className="num">{Number(row.quantity)}</td>
                      <td>{row.unit}</td>
                      <td className="num">{Number(row.cost_usd).toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Async>
    </section>
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
  const [costCategories, reloadCostCategories] = useApi(() => api.get<CostCategoriesOut>("/admin/billing/cost-categories"));
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
        <>
          <Async value={settings} reload={reloadSettings}>
            {(current) => <CloudRunForm key={current.updated_at ?? "new"} current={current} onSaved={reloadSettings} />}
          </Async>
          <Async value={costCategories} reload={reloadCostCategories}>
            {(current) => <CostCategoriesPanel current={current} onSaved={reloadCostCategories} />}
          </Async>
          <GcpImportPanel />
          <GcpReconciliationPanel />
          <GcpUnallocatedPanel />
        </>
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
      {tab === "plivo" && (
        <>
          <Async value={settings} reload={reloadSettings}>
            {(current) => <PlivoForm key={current.updated_at ?? "new"} current={current} onSaved={reloadSettings} />}
          </Async>
          <PlivoNumbersPanel />
        </>
      )}
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
        <Field label={t.billSellerPincode} value={form.seller_pincode} onChange={(v) => set("seller_pincode", v)} />
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
    <>
      <section className="card">
        <h2>{t.billExportTitle}</h2>
        <p className="empty" style={{ margin: "0 0 .5rem" }}>
          {t.billExportNote}
        </p>
        <label className="field">
          <span>{t.billExportStatus}</span>
          <select aria-label={t.billExportStatus} value={form.billing_export_status} onChange={(event) => set("billing_export_status", event.target.value as BillingSettings["billing_export_status"])}>
            <option value="not_connected">{t.billExportStatus_not_connected}</option>
            <option value="connected">{t.billExportStatus_connected}</option>
          </select>
        </label>
        <Field label={t.billExportRef} value={form.billing_export_ref} onChange={(v) => set("billing_export_ref", v)} />
        <label className="field">
          <span>{t.billReconciliationCycle}</span>
          <select aria-label={t.billReconciliationCycle} value={form.reconciliation_cycle} onChange={(event) => set("reconciliation_cycle", event.target.value as BillingSettings["reconciliation_cycle"])}>
            <option value="monthly">{t.billReconciliationCycle_monthly}</option>
            <option value="daily_preview_monthly_final">{t.billReconciliationCycle_daily_preview_monthly_final}</option>
          </select>
        </label>
        <label className="field">
          <span>{t.billUnallocatedHandling}</span>
          <select aria-label={t.billUnallocatedHandling} value={form.unallocated_handling} onChange={(event) => set("unallocated_handling", event.target.value as BillingSettings["unallocated_handling"])}>
            <option value="keep_unallocated">{t.billUnallocatedHandling_keep_unallocated}</option>
            <option value="manual_review">{t.billUnallocatedHandling_manual_review}</option>
          </select>
        </label>
        <label className="field">
          <span>{t.billSharedAllocationPolicy}</span>
          <select aria-label={t.billSharedAllocationPolicy} value={form.shared_allocation_policy} onChange={(event) => set("shared_allocation_policy", event.target.value as BillingSettings["shared_allocation_policy"])}>
            <option value="per_sku">{t.billSharedAllocationPolicy_per_sku}</option>
            <option value="usage_weighted">{t.billSharedAllocationPolicy_usage_weighted}</option>
          </select>
        </label>
      </section>
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
    </>
  );
}

function CostCategoriesPanel({ current, onSaved }: { current: CostCategoriesOut; onSaved: () => void }) {
  return (
    <section className="card">
      <h2>{t.billCostCatTitle}</h2>
      <p className="empty" style={{ margin: "0 0 .5rem" }}>
        {t.billCostCatNote}
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{t.billCostCatCategory}</th>
              <th>{t.billCostCatUnit}</th>
              <th className="num">{t.billCostCatUsdRate}</th>
              <th>{t.billCostCatAttribution}</th>
              <th>{t.billCostCatActive}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {current.categories.map((row) => (
              <CostCategoryRow key={row.category} existing={row} onSaved={onSaved} />
            ))}
            <CostCategoryRow existing={null} onSaved={onSaved} />
          </tbody>
        </table>
      </div>
    </section>
  );
}

function CostCategoryRow({ existing, onSaved }: { existing: CostCategory | null; onSaved: () => void }) {
  const toast = useToast();
  const [category, setCategory] = useState(existing?.category ?? "");
  const [unit, setUnit] = useState(existing?.unit ?? "");
  const [rate, setRate] = useState(existing ? String(Number(existing.usd_rate)) : "");
  const [attribution, setAttribution] = useState(existing?.attribution ?? "");
  const [active, setActive] = useState(existing?.active ?? true);
  const valid = category.trim() !== "" && (rate.trim() === "" || /^\d+(\.\d{1,6})?$/.test(rate.trim()));

  const save = async () => {
    try {
      await api.put<CostCategory>("/admin/billing/cost-categories", {
        category: category.trim(),
        unit: unit.trim(),
        usd_rate: rate.trim() === "" ? "0" : rate.trim(),
        attribution: attribution.trim(),
        active,
      });
      toast.info(t.billCostCatSaved);
      if (existing === null) {
        setCategory("");
        setUnit("");
        setRate("");
        setAttribution("");
        setActive(true);
      }
      onSaved();
    } catch (err) {
      toast.error(err);
    }
  };

  return (
    <tr>
      <td>
        <input aria-label={t.billCostCatCategory} value={category} onChange={(e) => setCategory(e.target.value)} placeholder={existing ? undefined : t.billCostCatAdd} disabled={existing !== null} />
      </td>
      <td>
        <input aria-label={`${t.billCostCatUnit} ${category}`} value={unit} onChange={(e) => setUnit(e.target.value)} />
      </td>
      <td className="num">
        <input aria-label={`${t.billCostCatUsdRate} ${category}`} value={rate} onChange={(e) => setRate(e.target.value)} placeholder="0.00" />
      </td>
      <td>
        <input aria-label={`${t.billCostCatAttribution} ${category}`} value={attribution} onChange={(e) => setAttribution(e.target.value)} />
      </td>
      <td>
        <input type="checkbox" aria-label={`${t.billCostCatActive} ${category}`} checked={active} onChange={(e) => setActive(e.target.checked)} />
      </td>
      <td>
        <button type="button" className="secondary" onClick={() => void save()} disabled={!valid}>
          {t.billCostCatSave}
        </button>
      </td>
    </tr>
  );
}

function GcpImportPanel() {
  const toast = useToast();
  const [period, setPeriod] = useState(previousMonthIst());
  const [raw, setRaw] = useState("");
  const [busy, setBusy] = useState(false);

  const parseRows = () => {
    const lines = raw.trim().split("\n").filter((l) => l.trim() !== "");
    if (lines.length < 2) return [];
    const header = (lines[0] ?? "").split(",").map((h) => h.trim());
    return lines.slice(1).map((line) => {
      const cells = line.split(",");
      const row: Record<string, string> = {};
      header.forEach((key, idx) => {
        row[key] = (cells[idx] ?? "").trim();
      });
      return row;
    });
  };

  const importRows = async () => {
    const rows = parseRows();
    if (rows.length === 0) {
      toast.error(new Error("No rows parsed - check header row and commas"));
      return;
    }
    setBusy(true);
    try {
      const out = await api.post<{ inserted: number; duplicate_skipped: number }>("/admin/billing/gcp/import", { period, rows });
      toast.info(`${out.inserted} ${t.billGcpImportDone}`);
      setRaw("");
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card">
      <h2>{t.billGcpImportTitle}</h2>
      <p className="empty" style={{ margin: "0 0 .5rem" }}>
        {t.billGcpImportSub}
      </p>
      <label className="field">
        <span>{t.billPeriod}</span>
        <input type="month" value={period} onChange={(e) => setPeriod(e.target.value)} />
      </label>
      <label className="field">
        <span>{t.billGcpImportPaste}</span>
        <textarea
          aria-label={t.billGcpImportPaste}
          rows={6}
          value={raw}
          onChange={(e) => setRaw(e.target.value)}
          placeholder="service,sku,usage_quantity,usage_unit,cost_usd,credits_usd,resource_name"
        />
      </label>
      <button type="button" onClick={() => void importRows()} disabled={busy || raw.trim() === ""}>
        {t.billGcpImportButton}
      </button>
    </section>
  );
}

function GcpReconciliationPanel() {
  const toast = useToast();
  const [period, setPeriod] = useState(previousMonthIst());
  const [result, setResult] = useState<GcpReconciliation | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const out = await api.get<GcpReconciliation | null>(`/admin/billing/gcp/reconcile/${period}`);
      setResult(out);
    } catch (err) {
      toast.error(err);
    }
  };

  const run = async () => {
    setBusy(true);
    try {
      const out = await api.post<GcpReconciliation>(`/admin/billing/gcp/reconcile/${period}`);
      setResult(out);
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card">
      <h2>{t.billGcpReconcileTitle}</h2>
      <div className="row">
        <label className="field">
          <span>{t.billPeriod}</span>
          <input type="month" value={period} onChange={(e) => setPeriod(e.target.value)} />
        </label>
        <button type="button" className="secondary" onClick={() => void load()}>
          {t.billRefresh}
        </button>
        <button type="button" onClick={() => void run()} disabled={busy}>
          {t.billGcpReconcileButton}
        </button>
      </div>
      {result && (
        <div className="table-wrap">
          <table>
            <tbody>
              <tr>
                <th>{t.billGcpReconcileImported}</th>
                <td className="num">{Number(result.imported_total_usd).toFixed(4)}</td>
              </tr>
              <tr>
                <th>{t.billGcpReconcileMetered}</th>
                <td className="num">{Number(result.metered_total_usd).toFixed(4)}</td>
              </tr>
              <tr>
                <th>{t.billGcpReconcileAllocated}</th>
                <td className="num">{Number(result.allocated_usd).toFixed(4)}</td>
              </tr>
              <tr>
                <th>{t.billGcpReconcileUnallocated}</th>
                <td className="num">{Number(result.unallocated_usd).toFixed(4)}</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function GcpUnallocatedPanel() {
  const toast = useToast();
  const [period, setPeriod] = useState(previousMonthIst());
  const [state, reload] = useApi(() => api.get<GcpUnallocatedListOut>(`/admin/billing/gcp/unallocated/${period}`), [period]);

  const resolve = async (id: string, resolved: boolean) => {
    try {
      await api.put(`/admin/billing/gcp/unallocated/${id}`, { resolved });
      reload();
    } catch (err) {
      toast.error(err);
    }
  };

  return (
    <section className="card">
      <h2>{t.billGcpUnallocatedTitle}</h2>
      <label className="field">
        <span>{t.billPeriod}</span>
        <input type="month" value={period} onChange={(e) => setPeriod(e.target.value)} />
      </label>
      <Async value={state} reload={reload}>
        {(data) =>
          data.items.length === 0 ? (
            <Empty>{t.billGcpUnallocatedEmpty}</Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t.billGcpUsageCategory}</th>
                    <th className="num">USD</th>
                    <th>Reason</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((row: GcpUnallocated) => (
                    <tr key={row.id}>
                      <td>{row.category}</td>
                      <td className="num">{Number(row.amount_usd).toFixed(4)}</td>
                      <td>
                        <small>{row.reason}</small>
                      </td>
                      <td>
                        {row.resolved ? (
                          <button type="button" className="secondary" onClick={() => void resolve(row.id, false)}>
                            {t.billGcpUnallocatedReopen}
                          </button>
                        ) : (
                          <button type="button" className="secondary" onClick={() => void resolve(row.id, true)}>
                            {t.billGcpUnallocatedResolve}
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

function PlivoForm({ current, onSaved }: { current: BillingSettings; onSaved: () => void }) {
  const { form, set, busy, save } = useSettingsForm(current);
  return (
    <section className="card">
      <h2>{t.billPlivoTitle}</h2>
      <p className="banner">{t.billPlivoNote}</p>
      <Field label={t.billPlivoOutbound} value={form.plivo_outbound_inr_per_min} onChange={(v) => set("plivo_outbound_inr_per_min", v)} />
      <Field label={t.billPlivoInbound} value={form.plivo_inbound_inr_per_min} onChange={(v) => set("plivo_inbound_inr_per_min", v)} />
      <Field label={t.billPlivoRental} value={form.plivo_number_rental_inr_per_month} onChange={(v) => set("plivo_number_rental_inr_per_month", v)} />
      <div className="row">
        <button type="button" onClick={() => void save(onSaved)} disabled={busy}>
          {t.save}
        </button>
        <Link to="/admin/billing">{t.billPlivoGoOverview}</Link>
      </div>
    </section>
  );
}

function PlivoNumbersPanel() {
  const toast = useToast();
  const [state, reload] = useApi(() => api.get<PlivoNumberAssignment[]>("/admin/billing/plivo/numbers"));
  const [profiles] = useApi(() => api.get<BillingProfile[]>("/admin/billing/profiles"));
  const [number, setNumber] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [assignedFrom, setAssignedFrom] = useState(new Date().toISOString().slice(0, 10));
  const [rate, setRate] = useState("");
  const [busy, setBusy] = useState(false);

  const profileList = profiles.state === "data" ? profiles.data : [];
  const profileById = new Map(profileList.map((p) => [p.tenant_id, p.tenant_name]));
  const valid = number.trim() !== "" && tenantId !== "" && assignedFrom !== "";

  const assign = async () => {
    if (!valid) return;
    setBusy(true);
    try {
      await api.post("/admin/billing/plivo/numbers", {
        plivo_number: number.trim(),
        tenant_id: tenantId,
        assigned_from: assignedFrom,
        monthly_rate_paise: rupeesToPaise(rate || "0") ?? 0,
      });
      toast.info(t.billPlivoAssignSaved);
      setNumber("");
      setRate("");
      reload();
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card">
      <h2>{t.billPlivoNumbersTitle}</h2>
      <p className="empty" style={{ margin: "0 0 .5rem" }}>
        {t.billPlivoNumbersSub}
      </p>
      <div className="row">
        <label className="field">
          <span>{t.billPlivoNumber}</span>
          <input value={number} onChange={(e) => setNumber(e.target.value)} placeholder="+91XXXXXXXXXX" />
        </label>
        <label className="field">
          <span>{t.billPlivoAssignTenant}</span>
          <select value={tenantId} onChange={(e) => setTenantId(e.target.value)}>
            <option value="">-</option>
            {profileList.map((p) => (
              <option key={p.tenant_id} value={p.tenant_id}>
                {p.tenant_name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>{t.billPlivoAssignFrom}</span>
          <input type="date" value={assignedFrom} onChange={(e) => setAssignedFrom(e.target.value)} />
        </label>
        <label className="field">
          <span>{t.billPlivoAssignRate}</span>
          <input value={rate} onChange={(e) => setRate(e.target.value)} placeholder="0" />
        </label>
        <button type="button" onClick={() => void assign()} disabled={!valid || busy}>
          {t.billPlivoAssignSave}
        </button>
      </div>
      <Async value={state} reload={reload}>
        {(rows) =>
          rows.length === 0 ? (
            <Empty>{t.billPlivoNumbersEmpty}</Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t.billPlivoNumber}</th>
                    <th>{t.billPlivoAssignTenant}</th>
                    <th>{t.billPlivoAssignFrom}</th>
                    <th>{t.billPlivoAssignTo}</th>
                    <th className="num">{t.billPlivoAssignRate}</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row: PlivoNumberAssignment) => (
                    <tr key={row.id}>
                      <td>{row.plivo_number}</td>
                      <td>{profileById.get(row.tenant_id) ?? row.tenant_id}</td>
                      <td>
                        <small>{row.assigned_from}</small>
                      </td>
                      <td>
                        <small>{row.assigned_to ?? "-"}</small>
                      </td>
                      <td className="num">{formatPaise(row.monthly_rate_paise)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Async>
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
    cloud_enabled: profile.cloud_enabled,
    llm_enabled: profile.llm_enabled,
    plivo_enabled: profile.plivo_enabled,
    payment_due_days: String(profile.payment_due_days),
    auto_generate_draft: profile.auto_generate_draft,
    billing_status: profile.billing_status,
  });
  const fee = rupeesToPaise(form.fee);
  const dueDays = /^\d{1,3}$/.test(form.payment_due_days.trim()) ? Number(form.payment_due_days) : null;
  const set = <K extends keyof typeof form>(key: K) => (value: (typeof form)[K]) => setForm({ ...form, [key]: value });

  const save = async () => {
    if (fee === null || dueDays === null) return;
    try {
      await api.put(`/admin/billing/profiles/${profile.tenant_id}`, {
        legal_name: form.legal_name,
        address: form.address,
        state_code: form.state_code,
        gstin: form.gstin,
        email: form.email,
        default_platform_fee_paise: fee,
        cloud_enabled: form.cloud_enabled,
        llm_enabled: form.llm_enabled,
        plivo_enabled: form.plivo_enabled,
        payment_due_days: dueDays,
        auto_generate_draft: form.auto_generate_draft,
        billing_status: form.billing_status,
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
      <h3>{t.billBrandUsageTitle}</h3>
      <p className="empty" style={{ margin: "0 0 .5rem" }}>
        {t.billBrandUsageSub}
      </p>
      <label className="field">
        <span>{t.billBrandCloudEnabled}</span>
        <input type="checkbox" aria-label={`${t.billBrandCloudEnabled} ${profile.slug}`} checked={form.cloud_enabled} onChange={(e) => set("cloud_enabled")(e.target.checked)} />
      </label>
      <label className="field">
        <span>{t.billBrandLlmEnabled}</span>
        <input type="checkbox" aria-label={`${t.billBrandLlmEnabled} ${profile.slug}`} checked={form.llm_enabled} onChange={(e) => set("llm_enabled")(e.target.checked)} />
      </label>
      <label className="field">
        <span>{t.billBrandPlivoEnabled}</span>
        <input type="checkbox" aria-label={`${t.billBrandPlivoEnabled} ${profile.slug}`} checked={form.plivo_enabled} onChange={(e) => set("plivo_enabled")(e.target.checked)} />
      </label>
      <h3>{t.billBrandInvoiceTitle}</h3>
      <Field label={`${t.billPaymentDueDays} (${profile.slug})`} value={form.payment_due_days} onChange={set("payment_due_days")} />
      <label className="field">
        <span>{t.billAutoGenerateDraft}</span>
        <select aria-label={`${t.billAutoGenerateDraft} ${profile.slug}`} value={form.auto_generate_draft ? "yes" : "no"} onChange={(e) => set("auto_generate_draft")(e.target.value === "yes")}>
          <option value="yes">{t.billYes}</option>
          <option value="no">{t.billNo}</option>
        </select>
      </label>
      <label className="field">
        <span>{t.billBillingStatus}</span>
        <select aria-label={`${t.billBillingStatus} ${profile.slug}`} value={form.billing_status} onChange={(e) => set("billing_status")(e.target.value as typeof form.billing_status)}>
          <option value="enabled">{t.billBillingStatus_enabled}</option>
          <option value="paused">{t.billBillingStatus_paused}</option>
        </select>
      </label>
      <button type="button" onClick={() => void save()} disabled={fee === null || dueDays === null} aria-label={`${t.save} ${profile.slug}`}>
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
  if (line.kind === "speech") {
    return `${line.detail.provider ?? ""} ${tokens(Number(line.detail.quantity ?? 0))} ${line.detail.unit ?? ""}`.trim();
  }
  return null;
}

function InvoiceView({ invoice, reload }: { invoice: Invoice; reload: () => void }) {
  const toast = useToast();
  const draft = invoice.status === "draft";
  const [fee, setFee] = useState(paiseToInput(invoice.platform_fee_paise));
  const [asking, setAsking] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [cancelReason, setCancelReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [payAmount, setPayAmount] = useState("");
  const [payDate, setPayDate] = useState(new Date().toISOString().slice(0, 10));
  const [payMethod, setPayMethod] = useState("");
  const [payNote, setPayNote] = useState("");
  const feePaise = rupeesToPaise(fee);
  const payAmountPaise = rupeesToPaise(payAmount);
  const half = Number(invoice.gst_rate_percent) / 2;
  const cancelled = invoice.cancelled_at !== null;

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
  const markPaid = (paid: boolean) => run(() => api.put(`/admin/billing/invoices/${invoice.id}/paid`, { paid }), paid ? t.billMarkPaidDone : t.billMarkUnpaidDone);
  const addPayment = () =>
    run(
      () =>
        api.post(`/admin/billing/invoices/${invoice.id}/payments`, {
          amount_paise: payAmountPaise,
          paid_on: payDate,
          method: payMethod,
          note: payNote,
        }),
      t.billPaymentSaved,
    );
  const deletePayment = (paymentId: string) => run(() => api.delete(`/admin/billing/invoices/${invoice.id}/payments/${paymentId}`), t.billPaymentDeleted);
  const cancelInvoice = () => run(() => api.post(`/admin/billing/invoices/${invoice.id}/cancel`, { reason: cancelReason }), t.billCancelDone);
  const uncancelInvoice = () => run(() => api.post(`/admin/billing/invoices/${invoice.id}/uncancel`), t.billUncancelDone);
  const downloadPdf = () => void downloadFile(`/admin/billing/invoices/${invoice.id}/pdf`, `${invoice.invoice_no ?? "invoice"}.pdf`);

  return (
    <section className="screen">
      <div className="no-print">
        <Link to="/admin/billing">{t.billBack}</Link>
        <h1>
          {invoice.tenant_name} - {invoice.period}{" "}
          <Chip tone={paymentStatusTone(invoice.payment_status)}>{t[`billPayStatus_${invoice.payment_status}` as Key]}</Chip>
        </h1>
        {!draft && (
          <div className="row">
            {invoice.due_date && (
              <span className="empty">
                {t.billDueDate}: {invoice.due_date}
              </span>
            )}
            {invoice.payment_status === "paid" ? (
              <button type="button" className="secondary" onClick={() => void markPaid(false)} disabled={busy}>
                {t.billMarkUnpaid}
              </button>
            ) : (
              <button type="button" className="secondary" onClick={() => void markPaid(true)} disabled={busy}>
                {t.billMarkPaid}
              </button>
            )}
            <button type="button" className="secondary" onClick={downloadPdf}>
              {t.billDownloadPdf}
            </button>
            {cancelled ? (
              <button type="button" className="secondary" onClick={() => void uncancelInvoice()} disabled={busy}>
                {t.billUncancelInvoice}
              </button>
            ) : (
              <button type="button" className="secondary" onClick={() => setCancelling(true)} disabled={busy}>
                {t.billCancelInvoice}
              </button>
            )}
          </div>
        )}
        {cancelled && (
          <div className="banner" role="alert">
            <strong>{t.billCancelledLabel}</strong>
            {invoice.cancelled_reason && <span> - {invoice.cancelled_reason}</span>}
          </div>
        )}
        {!draft && (
          <section className="card">
            <h2>{t.billPaymentsTitle}</h2>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t.billPaymentDate}</th>
                    <th className="num">{t.billAmount}</th>
                    <th>{t.billPaymentMethod}</th>
                    <th>{t.billPaymentNoteField}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {invoice.payments.length === 0 ? (
                    <tr>
                      <td colSpan={5}>
                        <Empty>{t.billPaymentsEmpty}</Empty>
                      </td>
                    </tr>
                  ) : (
                    invoice.payments.map((p) => (
                      <tr key={p.id}>
                        <td>{p.paid_on}</td>
                        <td className="num">{formatPaise(p.amount_paise)}</td>
                        <td>{p.method || "-"}</td>
                        <td>
                          <small>{p.note}</small>
                        </td>
                        <td>
                          <button type="button" className="secondary" onClick={() => void deletePayment(p.id)} disabled={busy}>
                            {t.billPaymentDelete}
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                  <tr>
                    <th colSpan={2} className="num">
                      {t.billBalanceDue}
                    </th>
                    <th colSpan={3} className="num">
                      {formatPaise(invoice.balance_due_paise)}
                    </th>
                  </tr>
                </tbody>
              </table>
            </div>
            {!cancelled && (
              <div className="row">
                <Field label={t.billPaymentAmount} value={payAmount} onChange={setPayAmount} placeholder="0.00" />
                <label className="field">
                  <span>{t.billPaymentDate}</span>
                  <input type="date" value={payDate} onChange={(e) => setPayDate(e.target.value)} />
                </label>
                <Field label={t.billPaymentMethod} value={payMethod} onChange={setPayMethod} placeholder="UPI / bank" />
                <Field label={t.billPaymentNoteField} value={payNote} onChange={setPayNote} />
                <button type="button" onClick={() => void addPayment()} disabled={busy || payAmountPaise === null || payAmountPaise <= 0}>
                  {t.billPaymentAdd}
                </button>
              </div>
            )}
          </section>
        )}
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
          <div className="card">
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
      {cancelling && (
        <div className="dialog no-print" role="dialog" aria-modal="true" aria-label={t.billCancelInvoice}>
          <div className="card">
            <h2>{t.billCancelInvoice}</h2>
            <Field label={t.billCancelReason} value={cancelReason} onChange={setCancelReason} />
            <div className="row">
              <button type="button" onClick={() => void cancelInvoice()} disabled={busy}>
                {t.confirm}
              </button>
              <button type="button" className="secondary" onClick={() => setCancelling(false)}>
                {t.cancel}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
