import { useState } from "react";
import { api } from "../api/client";
import type { Policy, PolicyContent, PolicyTryResult, Regression, WriteTool } from "../api/types";
import { Async, Chip, Field, Screen, useSubmit } from "../components/common";
import { Icon } from "../components/Icons";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { rupeesToPaise, formatPaise, formatIst } from "../format";
import { t } from "../i18n";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const WRITE_TOOLS = ["address_update", "order_cancel", "exchange_request", "return_request", "refund_create"] as const;

function clampNum(value: string, min: number, max: number, fallback: number, integer: boolean): number {
  const n = Number(value);
  if (value.trim() === "" || !Number.isFinite(n)) return fallback;
  const bounded = Math.min(max, Math.max(min, n));
  return integer ? Math.trunc(bounded) : Math.round(bounded * 100) / 100;
}

// Only writes a field when the brand already had it or changed it from the backend default,
// so older saved policies are not rewritten with extra keys. Safety dials are never touched while locked.
function advancedValues(
  c: PolicyContent,
  v: { tone: string; confirmTtl: string; sentiment: string; fraudWindow: string; fraudLimit: string; keepSafety: boolean },
): Partial<PolicyContent> {
  const out: Partial<PolicyContent> = {};
  if (c.tone_notes !== undefined || v.tone !== "") out.tone_notes = v.tone;
  const ttl = clampNum(v.confirmTtl, 5, 240, c.confirm_ttl_minutes ?? 30, true);
  if (c.confirm_ttl_minutes !== undefined || ttl !== 30) out.confirm_ttl_minutes = ttl;
  if (!v.keepSafety) {
    const mood = clampNum(v.sentiment, -1, 0, c.sentiment_min ?? -0.4, false);
    if (c.sentiment_min !== undefined || mood !== -0.4) out.sentiment_min = mood;
    const win = clampNum(v.fraudWindow, 1, 168, c.fraud_cluster_window_hours ?? 24, true);
    if (c.fraud_cluster_window_hours !== undefined || win !== 24) out.fraud_cluster_window_hours = win;
    const lim = clampNum(v.fraudLimit, 2, 20, c.fraud_cluster_claim_limit ?? 3, true);
    if (c.fraud_cluster_claim_limit !== undefined || lim !== 3) out.fraud_cluster_claim_limit = lim;
  }
  return out;
}

class FormError extends Error {}

function parseTagWindows(value: string): Record<string, number> {
  const out: Record<string, number> = {};
  for (const part of parseList(value)) {
    const [tag, days, ...rest] = part.split("=").map((x) => x.trim());
    const n = Number(days);
    if (!tag || rest.length > 0 || !Number.isInteger(n) || n < 1 || n > 60) throw new FormError(t.tagWindowsInvalid);
    out[tag] = n;
  }
  return out;
}

function formatTagWindows(map: Record<string, number> | undefined): string {
  return Object.entries(map ?? {})
    .map(([tag, days]) => `${tag}=${days}`)
    .join(", ");
}

type DayRow = { start: string; end: string };
type FaqRow = { question: string; answer: string };
type CodMethod = "" | "bank_transfer" | "store_credit";

// Same rule as advancedValues: a field is written only if the brand already had it or set it,
// so older saved policies are not rewritten with empty extras.
function groupValues(
  c: PolicyContent,
  v: {
    holidays: string;
    dayRows: Record<string, DayRow>;
    tagWindows: string;
    faqs: FaqRow[];
    ooo: string;
    codMethod: CodMethod;
    codNote: string;
    reminder: string;
  },
): Partial<PolicyContent> {
  const out: Partial<PolicyContent> = {};
  const faqs: FaqRow[] = [];
  for (const row of v.faqs) {
    const question = row.question.trim();
    const answer = row.answer.trim();
    if (!question && !answer) continue;
    if (!question || !answer) throw new FormError(t.faqIncomplete);
    faqs.push({ question, answer });
  }
  if (c.faqs !== undefined || faqs.length > 0) out.faqs = faqs;
  const ooo = v.ooo.trim();
  if (c.ooo_message !== undefined || ooo !== "") out.ooo_message = ooo;
  const note = v.codNote.trim();
  if (c.cod_refund_method !== undefined || v.codMethod !== "") out.cod_refund_method = v.codMethod;
  if (c.cod_refund_note !== undefined || note !== "") out.cod_refund_note = note;
  const reminder = Number(v.reminder.trim() === "" ? "0" : v.reminder);
  if (!Number.isInteger(reminder) || reminder < 0 || reminder > 1440 || (reminder > 0 && reminder < 5)) {
    throw new FormError(t.reminderInvalid);
  }
  if (c.handoff_reminder_minutes !== undefined || reminder !== 0) out.handoff_reminder_minutes = reminder;
  const holidays = parseList(v.holidays);
  if (c.holidays !== undefined || holidays.length > 0) out.holidays = holidays;
  const dayHours: Record<string, DayRow> = {};
  for (const [day, row] of Object.entries(v.dayRows)) {
    const start = row.start.trim();
    const end = row.end.trim();
    if (!start && !end) continue;
    if (!start || !end) throw new FormError(t.dayHoursIncomplete);
    dayHours[day] = { start, end };
  }
  if (c.day_hours !== undefined || Object.keys(dayHours).length > 0) out.day_hours = dayHours;
  const windows = parseTagWindows(v.tagWindows);
  if (c.return_window_by_tag !== undefined || Object.keys(windows).length > 0) out.return_window_by_tag = windows;
  return out;
}

export function parseList(value: string): string[] {
  return value
    .split(",")
    .map((x) => x.trim())
    .filter((x) => x.length > 0);
}

function Editor({ policy, onSaved }: { policy: Policy; onSaved: () => void }) {
  const toast = useToast();
  const [c, setC] = useState<PolicyContent>(policy.content);
  const [tags, setTags] = useState(policy.content.final_sale_tags.join(", "));
  const [keywords, setKeywords] = useState(policy.content.extra_handoff_keywords.join(", "));
  const [refundLimit, setRefundLimit] = useState(
    policy.content.refund_auto_limit_paise == null ? "" : String(policy.content.refund_auto_limit_paise / 100),
  );
  const [tone, setTone] = useState(policy.content.tone_notes ?? "");
  const [confirmTtl, setConfirmTtl] = useState(String(policy.content.confirm_ttl_minutes ?? 30));
  const [sentiment, setSentiment] = useState(String(policy.content.sentiment_min ?? -0.4));
  const [fraudWindow, setFraudWindow] = useState(String(policy.content.fraud_cluster_window_hours ?? 24));
  const [fraudLimit, setFraudLimit] = useState(String(policy.content.fraud_cluster_claim_limit ?? 3));
  const [faqRows, setFaqRows] = useState<FaqRow[]>((policy.content.faqs ?? []).map((f) => ({ ...f })));
  const [ooo, setOoo] = useState(policy.content.ooo_message ?? "");
  const [codMethod, setCodMethod] = useState<CodMethod>(policy.content.cod_refund_method ?? "");
  const [codNote, setCodNote] = useState(policy.content.cod_refund_note ?? "");
  const [reminder, setReminder] = useState(String(policy.content.handoff_reminder_minutes ?? 0));
  const [reason, setReason] = useState("");
  const [holidays, setHolidays] = useState((policy.content.holidays ?? []).join(", "));
  const [dayRows, setDayRows] = useState<Record<string, DayRow>>(() => {
    const rows: Record<string, DayRow> = {};
    DAYS.forEach((_, day) => {
      const row = policy.content.day_hours?.[String(day)];
      rows[String(day)] = { start: row?.start ?? "", end: row?.end ?? "" };
    });
    return rows;
  });
  const [tagWindows, setTagWindows] = useState(formatTagWindows(policy.content.return_window_by_tag));
  const [regression, setRegression] = useState<Regression | null>(null);
  const [confirmPublish, setConfirmPublish] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const locked = policy.status !== "draft";
  const setDay = (day: number, key: keyof DayRow, value: string) =>
    setDayRows((rows) => ({ ...rows, [String(day)]: { start: "", end: "", ...rows[String(day)], [key]: value } }));
  const anyAuto = WRITE_TOOLS.some((tool) => c.autonomy?.[tool] === "auto");
  const num = (value: string, fallback: number) => {
    const n = Number(value);
    return Number.isFinite(n) ? Math.trunc(n) : fallback;
  };

  const save = useSubmit(async () => {
    setFormError(null);
    const limitPaise = refundLimit.trim() === "" ? null : rupeesToPaise(refundLimit);
    const common = { ...c, final_sale_tags: parseList(tags), extra_handoff_keywords: parseList(keywords) };
    const next: PolicyContent =
      c.schema_version === 2
        ? {
            ...common,
            ...advancedValues(c, {
              tone: tone.slice(0, 500),
              confirmTtl,
              sentiment,
              fraudWindow,
              fraudLimit,
              keepSafety: anyAuto,
            }),
            ...groupValues(c, { holidays, dayRows, tagWindows, faqs: faqRows, ooo, codMethod, codNote, reminder }),
          }
        : {
            ...common,
            refund_auto_limit_paise: limitPaise,
            refund_customer_daily_limit_paise: limitPaise === null ? null : (c.refund_customer_daily_limit_paise ?? limitPaise),
          };
    await api.put<Policy>(`/policies/${policy.id}`, {
      content: next,
      base_version: policy.version,
      ...(reason.trim() ? { reason: reason.trim() } : {}),
    });
    toast.info(t.saved);
    onSaved();
  }, (err) => {
    if (err instanceof FormError) setFormError(err.message);
    else toast.error(err);
  });

  const run = useSubmit(async () => {
    setRegression(await api.get<Regression>(`/policies/${policy.id}/regression`));
  }, toast.error);

  const publish = useSubmit(async () => {
    await api.post<Policy>(`/policies/${policy.id}/publish`);
    setConfirmPublish(false);
    toast.info(t.policyPublished);
    onSaved();
  }, toast.error);

  return (
    <div className="policy-layout">
      <div className="card policy-main">
        <div className="row">
          <h2 className="policy-section-title">Configuration Settings</h2>
          <Chip tone={policy.status === "published" ? "ok" : undefined}>
            {policy.status === "draft" ? t.policyDraft : policy.status === "published" ? t.policyPublished : t.policyArchived}
          </Chip>
          <small>{formatIst(policy.published_at ?? policy.created_at)}</small>
        </div>
        <fieldset disabled={locked} style={{ border: 0, padding: 0 }}>
          <div className="policy-fields">
            <Field label={t.returnWindow} type="number" value={String(c.return_window_days)} onChange={(v) => setC({ ...c, return_window_days: num(v, 7) })} />
            <Field label={t.slaMinutes} type="number" value={String(c.handoff_sla_minutes)} onChange={(v) => setC({ ...c, handoff_sla_minutes: num(v, 30) })} />
            <Field label={t.customerCap} type="number" value={String(c.customer_daily_msg_cap)} onChange={(v) => setC({ ...c, customer_daily_msg_cap: num(v, 30) })} />
            <Field label={t.brandCap} type="number" value={String(c.brand_daily_msg_cap)} onChange={(v) => setC({ ...c, brand_daily_msg_cap: num(v, 2000) })} />
          </div>
          <div className="office-grid">
            <Field label={t.officeStart} type="time" value={c.office_hours.start} onChange={(v) => setC({ ...c, office_hours: { ...c.office_hours, start: v } })} />
            <Field label={t.officeEnd} type="time" value={c.office_hours.end} onChange={(v) => setC({ ...c, office_hours: { ...c.office_hours, end: v } })} />
            <div className="field full">
              <span className="field-label">{t.officeDays}</span>
              <div className="row">
                {DAYS.map((label, day) => (
                  <label key={label} className="row">
                    <input
                      type="checkbox"
                      style={{ width: "auto" }}
                      checked={c.office_hours.days.includes(day)}
                      onChange={(e) => {
                        const days = e.target.checked ? [...c.office_hours.days, day] : c.office_hours.days.filter((d) => d !== day);
                        setC({ ...c, office_hours: { ...c.office_hours, days: days.sort() } });
                      }}
                    />
                    {label}
                  </label>
                ))}
              </div>
            </div>
          </div>
          {c.schema_version === 2 && (
            <>
              <Field label={t.holidaysLabel} value={holidays} onChange={setHolidays} placeholder="2026-11-12, 2026-11-14" />
              <small className="help">{t.holidaysHelp}</small>
              <h3 className="policy-subhead">{t.dayHoursHeading}</h3>
              <small className="help">{t.dayHoursHelp}</small>
              <div className="policy-fields">
                {DAYS.map((label, day) =>
                  c.office_hours.days.includes(day) ? (
                    <div key={label} className="row">
                      <strong style={{ minWidth: 40 }}>{label}</strong>
                      <input
                        type="time"
                        aria-label={`${label} start`}
                        value={dayRows[String(day)]?.start ?? ""}
                        onChange={(e) => setDay(day, "start", e.target.value)}
                      />
                      <input
                        type="time"
                        aria-label={`${label} end`}
                        value={dayRows[String(day)]?.end ?? ""}
                        onChange={(e) => setDay(day, "end", e.target.value)}
                      />
                    </div>
                  ) : null,
                )}
              </div>
            </>
          )}
          <div className="policy-fields">
            <Field label={t.finalSaleTags} value={tags} onChange={setTags} />
            <Field label={t.handoffKeywords} value={keywords} onChange={setKeywords} />
            {c.schema_version !== 2 && <Field label="Auto refund limit (₹, blank = off)" value={refundLimit} onChange={setRefundLimit} />}
          </div>
          {c.schema_version === 2 && (
            <>
              <Field label={t.tagWindows} value={tagWindows} onChange={setTagWindows} placeholder="shoes=15, sale=3" />
              <small className="help">{t.tagWindowsHelp}</small>
              <h3 className="policy-subhead">{t.faqHeading}</h3>
              <small className="help">{t.faqHelp}</small>
              {faqRows.map((row, index) => (
                <div key={index} className="policy-fields">
                  <Field
                    label={`${t.faqQuestion} ${index + 1}`}
                    value={row.question}
                    onChange={(v) => setFaqRows((rows) => rows.map((r, i) => (i === index ? { ...r, question: v } : r)))}
                  />
                  <Field
                    label={`${t.faqAnswer} ${index + 1}`}
                    value={row.answer}
                    onChange={(v) => setFaqRows((rows) => rows.map((r, i) => (i === index ? { ...r, answer: v } : r)))}
                  />
                  <div>
                    <button type="button" className="secondary" onClick={() => setFaqRows((rows) => rows.filter((_, i) => i !== index))}>
                      {t.faqRemove}
                    </button>
                  </div>
                </div>
              ))}
              {faqRows.length < 20 && (
                <div>
                  <button type="button" className="secondary" onClick={() => setFaqRows((rows) => [...rows, { question: "", answer: "" }])}>
                    {t.faqAdd}
                  </button>
                </div>
              )}
              <h3 className="policy-subhead">{t.codHeading}</h3>
              <div className="policy-fields">
                <label className="field">
                  <span className="field-label">{t.codMethod}</span>
                  <select aria-label={t.codMethod} value={codMethod} onChange={(e) => setCodMethod(e.target.value as CodMethod)}>
                    <option value="">{t.codNone}</option>
                    <option value="bank_transfer">{t.codBank}</option>
                    <option value="store_credit">{t.codCredit}</option>
                  </select>
                </label>
                <Field label={t.codNote} value={codNote} onChange={setCodNote} />
              </div>
              <small className="help">{t.codHelp}</small>
              <Field label={t.oooMessage} value={ooo} onChange={setOoo} />
              <small className="help">{t.oooHelp}</small>
              <Field label={t.reminderLabel} type="number" value={reminder} onChange={setReminder} />
              <small className="help">{t.reminderHelp}</small>
            </>
          )}
          <div className="disclosure-grid">
            {/* Customer-facing replies (WhatsApp/email) are English-only (pick_language() always
                resolves to "en"), so only the English disclosure is shown here. The Hindi/hinglish
                values in c.ai_disclosure are left untouched in state (not editable here) rather than
                removed from the schema, since app/voice/service.py still renders handoff/call-summary
                templates in the caller's own language for voice calls - deleting them from the model
                would break that channel. See DECISIONS.md "Section 3" / Hindi-cleanup entry. */}
            <Field label={t.disclosureEn} value={c.ai_disclosure.en} onChange={(v) => setC({ ...c, ai_disclosure: { ...c.ai_disclosure, en: v } })} />
          </div>
          {c.schema_version === 2 && (
            <label className="field">
              <span className="field-label">{t.replyLanguage}</span>
              <select
                aria-label={t.replyLanguage}
                value={c.reply_language ?? "en"}
                onChange={(event) => setC({ ...c, reply_language: event.target.value as "hi" | "en" | "hinglish" })}
              >
                <option value="en">{t.replyLanguageEn}</option>
                <option value="hi">{t.replyLanguageHi}</option>
                <option value="hinglish">{t.replyLanguageHinglish}</option>
              </select>
            </label>
          )}
          {c.schema_version === 2 && (
            <>
              <h3 className="policy-subhead">{t.advancedHeading}</h3>
              <label className="field">
                <span className="field-label">{t.toneNotes}</span>
                <textarea aria-label={t.toneNotes} maxLength={500} value={tone} onChange={(event) => setTone(event.target.value)} />
                <small className="help">{t.toneNotesHelp}</small>
              </label>
              <div className="policy-fields">
                <div>
                  <Field label={t.confirmTtl} type="number" value={confirmTtl} onChange={setConfirmTtl} />
                  <small className="help">{t.confirmTtlHelp}</small>
                </div>
              </div>
              <h3 className="policy-subhead">{t.safetyHeading}</h3>
              {anyAuto && <small className="help">{t.safetyLocked}</small>}
              <fieldset disabled={anyAuto} style={{ border: 0, padding: 0, margin: 0 }}>
                <div className="policy-fields">
                  <div>
                    <Field label={t.sentimentMin} type="number" value={sentiment} onChange={setSentiment} />
                    <small className="help">{t.sentimentMinHelp}</small>
                  </div>
                  <div>
                    <Field label={t.fraudWindow} type="number" value={fraudWindow} onChange={setFraudWindow} />
                    <small className="help">{t.fraudWindowHelp}</small>
                  </div>
                  <div>
                    <Field label={t.fraudClaimLimit} type="number" value={fraudLimit} onChange={setFraudLimit} />
                    <small className="help">{t.fraudClaimLimitHelp}</small>
                  </div>
                </div>
              </fieldset>
            </>
          )}
        </fieldset>
        {c.schema_version !== 2 && c.refund_auto_limit_paise != null && <small className="help">{formatPaise(c.refund_auto_limit_paise)}</small>}
        {!locked && <Field label={t.changeReason} value={reason} onChange={setReason} />}
        {!locked && (
          <div className="row policy-actions">
            <button type="button" disabled={save.busy} onClick={() => void save.run()}>
              {t.save}
            </button>
            <button type="button" className="secondary" disabled={run.busy} onClick={() => void run.run()}>
              {t.policyRunRegression}
            </button>
            <button type="button" disabled={regression?.passed !== true} onClick={() => setConfirmPublish(true)}>
              {t.policyPublish}
            </button>
          </div>
        )}
        {formError && <div className="error-box">{formError}</div>}
        <TryPanel policyId={policy.id} />
        {regression && (
          <div className={regression.passed ? "banner" : "error-box"}>
            <strong>{regression.passed ? t.policyRegressionPassed : t.policyRegressionFailed}</strong> ({regression.scenarios_total})
            <ul>
              {regression.scenarios_failed.map((name) => (
                <li key={name}>{name}</li>
              ))}
            </ul>
          </div>
        )}
        {confirmPublish && (
          <div className="dialog" role="dialog" aria-modal="true">
            <div className="card pad">
              <p>{t.policyPublishConfirm}</p>
              <div className="row">
                <button type="button" disabled={publish.busy} onClick={() => void publish.run()}>
                  {t.policyPublish}
                </button>
                <button type="button" className="secondary" onClick={() => setConfirmPublish(false)}>
                  {t.cancel}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
      <div className="card quick-tips">
        <h2>
          <Icon name="info" /> {t.policyTipsTitle}
        </h2>
        <p className="help">{t.policyTipsBody}</p>
      </div>
    </div>
  );
}

const TRY_TOOLS: WriteTool[] = ["address_update", "order_cancel", "exchange_request", "return_request", "refund_create"];

function TryPanel({ policyId }: { policyId: string }) {
  const toast = useToast();
  const [tool, setTool] = useState<WriteTool>("order_cancel");
  const [total, setTotal] = useState("500");
  const [status, setStatus] = useState<"unfulfilled" | "fulfilled">("unfulfilled");
  const [days, setDays] = useState("2");
  const [tags, setTags] = useState("");
  const [recent, setRecent] = useState(false);
  const [result, setResult] = useState<PolicyTryResult | null>(null);
  const check = useSubmit(async () => {
    const rupees = Number(total);
    const delivered = Number(days);
    const body = {
      tool,
      order_total_rupees: Number.isFinite(rupees) ? Math.max(0, Math.trunc(rupees)) : 0,
      fulfillment_status: status,
      delivered_days_ago: status === "fulfilled" && Number.isFinite(delivered) ? Math.max(0, Math.trunc(delivered)) : null,
      tags: parseList(tags),
      refunded_in_last_30_days: recent,
    };
    setResult(await api.post<PolicyTryResult>(`/policies/${policyId}/try`, body));
  }, toast.error);
  const routeText = {
    auto: t.tryRouteAuto,
    approval: t.tryRouteApproval,
    handoff: t.tryRouteHandoff,
    decline: t.tryRouteDecline,
  };
  return (
    <div className="card pad">
      <h3 className="policy-subhead">{t.tryHeading}</h3>
      <small className="help">{t.tryHelp}</small>
      <div className="policy-fields">
        <label className="field">
          <span className="field-label">{t.tryTool}</span>
          <select aria-label={t.tryTool} value={tool} onChange={(e) => setTool(e.target.value as WriteTool)}>
            {TRY_TOOLS.map((name) => (
              <option key={name} value={name}>
                {t[`tool_${name}`]}
              </option>
            ))}
          </select>
        </label>
        <Field label={t.tryTotal} type="number" value={total} onChange={setTotal} />
        <label className="field">
          <span className="field-label">{t.tryStatus}</span>
          <select aria-label={t.tryStatus} value={status} onChange={(e) => setStatus(e.target.value as "unfulfilled" | "fulfilled")}>
            <option value="unfulfilled">{t.tryNotShipped}</option>
            <option value="fulfilled">{t.tryDelivered}</option>
          </select>
        </label>
        {status === "fulfilled" && <Field label={t.tryDays} type="number" value={days} onChange={setDays} />}
        <Field label={t.tryTags} value={tags} onChange={setTags} />
        <label className="row">
          <input type="checkbox" style={{ width: "auto" }} checked={recent} onChange={(e) => setRecent(e.target.checked)} />
          {t.tryRecentRefund}
        </label>
      </div>
      <button type="button" className="secondary" disabled={check.busy} onClick={() => void check.run()}>
        {t.tryRun}
      </button>
      {result && (
        <div className="banner" role="status">
          <strong>{routeText[result.route]}</strong>
          <div>
            {t.tryReason}: {result.reason}
            {result.handoff_reason ? ` (${result.handoff_reason})` : ""}
          </div>
          <div>
            {t.tryWindow}: {result.return_window_days}
          </div>
          <div>
            {result.office_open ? t.tryOpen : t.tryClosed}
            {!result.office_open && result.next_open ? `. ${t.tryOpensAt}: ${formatIst(result.next_open)}` : ""}
          </div>
        </div>
      )}
    </div>
  );
}

function HistoryModal({
  items,
  onClose,
  onRestore,
}: {
  items: Policy[];
  onClose: () => void;
  onRestore: ((policy: Policy) => void) | null;
}) {
  const [openId, setOpenId] = useState<string | null>(null);
  const open = items.find((p) => p.id === openId) ?? null;
  return (
    <div className="dialog" role="dialog" aria-modal="true">
      <div className="card dialog-card">
        <div className="row dialog-card-header">
          <h2>{open ? `v${open.version}` : t.policyHistoryTitle}</h2>
          <div className="row">
            {open && onRestore && (
              <button type="button" onClick={() => onRestore(open)}>
                {t.policyRestore}
              </button>
            )}
            {open && !onRestore && <small className="help">{t.policyRestoreNeedsNoDraft}</small>}
            {open && (
              <button type="button" className="secondary" onClick={() => setOpenId(null)}>
                {t.policyHistoryBack}
              </button>
            )}
            <button type="button" className="secondary" onClick={onClose}>
              {t.cancel}
            </button>
          </div>
        </div>
        {open ? (
          <Editor key={open.id} policy={open} onSaved={() => undefined} />
        ) : (
          <>
            {items.length === 0 && <p className="empty">{t.policyNoHistory}</p>}
            {items.map((p) => (
              <button
                key={p.id}
                type="button"
                className="secondary row card pad"
                style={{ width: "100%", justifyContent: "space-between" }}
                onClick={() => setOpenId(p.id)}
              >
                <strong>v{p.version}</strong>
                <Chip tone={p.status === "published" ? "ok" : undefined}>
                  {p.status === "draft" ? t.policyDraft : p.status === "published" ? t.policyPublished : t.policyArchived}
                </Chip>
                <small>{formatIst(p.published_at ?? p.created_at)}</small>
              </button>
            ))}
          </>
        )}
      </div>
    </div>
  );
}

export function PolicySettings() {
  const toast = useToast();
  const [list, reload] = useApi(() => api.get<Policy[]>("/policies"));
  const [showHistory, setShowHistory] = useState(false);
  const create = useSubmit(async () => {
    // Base the new draft's CONTENT on whichever policy is actually LIVE (status
    // "published"), never on list.data[0] (the highest version number). After a
    // manual/emergency rollback the published row can have a lower version than some
    // archived ones, so "highest version" and "currently published" are not the same row -
    // using the wrong one silently resends stale (possibly archived, since-reverted)
    // autonomy/limits data and the backend rejects it as an unauthorized "raise to auto"
    // (E-POL-002 auto_needs_admin / limit_raise_needs_admin).
    //
    // base_version (the optimistic-lock check) is a SEPARATE concern: the backend compares
    // it against the table-wide highest version number (any status), not against the
    // published row's version - same split AutonomySettings below already uses (published
    // row for content, Math.max(...versions) for base_version). Sending the published row's
    // own version as base_version here would permanently 409 (E-CONF-001) in that same
    // rolled-back-tenant case, since published.version < table max forever until a new
    // version is published.
    const items = list.state === "data" ? list.data : [];
    const latest = items.find((p) => p.status === "published");
    const base = latest?.content;
    const maxVersion = items.length > 0 ? Math.max(...items.map((p) => p.version)) : null;
    await api.post<Policy>("/policies", {
      content: base ?? DEFAULT_POLICY,
      base_version: maxVersion,
    });
    reload();
  }, toast.error);
  const restore = async (p: Policy) => {
    try {
      const items = list.state === "data" ? list.data : [];
      const maxVersion = items.length > 0 ? Math.max(...items.map((x) => x.version)) : null;
      await api.post<Policy>("/policies", {
        content: p.content,
        base_version: maxVersion,
        reason: `Restored version ${p.version}`,
      });
      toast.info(t.policyRestored);
      setShowHistory(false);
      reload();
    } catch (err) {
      toast.error(err);
    }
  };
  return (
    <Screen title={t.policyTitle}>
      <Async value={list} reload={reload}>
        {(items) => {
          // Sirf live policy (draft agar edit chal raha hai, warna published) saamne
          // dikhti hai. Baaki (archived / purani) "Version history" ke peeche rehti hain.
          const draft = items.find((p) => p.status === "draft");
          const published = items.find((p) => p.status === "published");
          const current = draft ?? published;
          const history = items.filter((p) => p.status !== "draft" && p.id !== current?.id);
          return (
            <>
              {history.length > 0 && (
                <button type="button" className="secondary" onClick={() => setShowHistory(true)}>
                  {t.policyHistoryLink}
                </button>
              )}
              {current ? (
                <Editor key={`${current.id}-${current.status}`} policy={current} onSaved={reload} />
              ) : (
                <p className="empty">{t.policyEmpty}</p>
              )}
              {!draft && (
                <button type="button" disabled={create.busy} onClick={() => void create.run()}>
                  {current ? t.policyEdit : t.policyNew}
                </button>
              )}
              {showHistory && (
                <HistoryModal items={history} onClose={() => setShowHistory(false)} onRestore={draft ? null : (p) => void restore(p)} />
              )}
            </>
          );
        }}
      </Async>
    </Screen>
  );
}

export const DEFAULT_POLICY: PolicyContent = {
  schema_version: 1,
  office_hours: { start: "09:00", end: "21:00", days: [0, 1, 2, 3, 4, 5, 6] },
  return_window_days: 7,
  final_sale_tags: [],
  refund_auto_limit_paise: null,
  refund_customer_daily_limit_paise: null,
  customer_daily_msg_cap: 30,
  brand_daily_msg_cap: 2000,
  handoff_sla_minutes: 30,
  ai_disclosure: {
    hi: "Main ek AI sahayak hoon. Zarurat padne par hamari team aapse baat karegi.",
    en: "I'm an AI assistant. Our team will step in if needed.",
    hinglish: "Main ek AI assistant hoon. Zarurat padi to hamari team aapse baat karegi.",
  },
  extra_handoff_keywords: [],
  enabled_tools: ["handoff_to_human", "order_lookup", "tracking_lookup"],
};
