import { useState } from "react";
import { api } from "../api/client";
import type { Policy, PolicyContent, Regression } from "../api/types";
import { Async, Chip, Field, Screen, useSubmit } from "../components/common";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { rupeesToPaise, formatPaise, formatIst } from "../format";
import { t } from "../i18n";

const DAYS = ["Som", "Mang", "Budh", "Guru", "Shukra", "Shani", "Ravi"];

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
  const [regression, setRegression] = useState<Regression | null>(null);
  const [confirmPublish, setConfirmPublish] = useState(false);
  const locked = policy.status !== "draft";
  const num = (value: string, fallback: number) => {
    const n = Number(value);
    return Number.isFinite(n) ? Math.trunc(n) : fallback;
  };

  const save = useSubmit(async () => {
    const limitPaise = refundLimit.trim() === "" ? null : rupeesToPaise(refundLimit);
    const common = { ...c, final_sale_tags: parseList(tags), extra_handoff_keywords: parseList(keywords) };
    const next: PolicyContent =
      c.schema_version === 2
        ? common
        : {
            ...common,
            refund_auto_limit_paise: limitPaise,
            refund_customer_daily_limit_paise: limitPaise === null ? null : (c.refund_customer_daily_limit_paise ?? limitPaise),
          };
    await api.put<Policy>(`/policies/${policy.id}`, { content: next, base_version: policy.version });
    toast.info(t.saved);
    onSaved();
  }, toast.error);

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
    <div className="card">
      <div className="row">
        <h2>v{policy.version}</h2>
        <Chip tone={policy.status === "published" ? "ok" : undefined}>
          {policy.status === "draft" ? t.policyDraft : policy.status === "published" ? t.policyPublished : t.policyArchived}
        </Chip>
        <small>{formatIst(policy.published_at ?? policy.created_at)}</small>
      </div>
      <fieldset disabled={locked} style={{ border: 0, padding: 0 }}>
        <Field label={t.returnWindow} type="number" value={String(c.return_window_days)} onChange={(v) => setC({ ...c, return_window_days: num(v, 7) })} />
        <Field label={t.slaMinutes} type="number" value={String(c.handoff_sla_minutes)} onChange={(v) => setC({ ...c, handoff_sla_minutes: num(v, 30) })} />
        <Field label={t.customerCap} type="number" value={String(c.customer_daily_msg_cap)} onChange={(v) => setC({ ...c, customer_daily_msg_cap: num(v, 30) })} />
        <Field label={t.brandCap} type="number" value={String(c.brand_daily_msg_cap)} onChange={(v) => setC({ ...c, brand_daily_msg_cap: num(v, 2000) })} />
        <Field label={t.officeStart} type="time" value={c.office_hours.start} onChange={(v) => setC({ ...c, office_hours: { ...c.office_hours, start: v } })} />
        <Field label={t.officeEnd} type="time" value={c.office_hours.end} onChange={(v) => setC({ ...c, office_hours: { ...c.office_hours, end: v } })} />
        <div className="field">
          <span>{t.officeDays}</span>
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
        <Field label={t.finalSaleTags} value={tags} onChange={setTags} />
        <Field label={t.handoffKeywords} value={keywords} onChange={setKeywords} />
        {c.schema_version !== 2 && <Field label="Auto refund limit (₹, khali = band)" value={refundLimit} onChange={setRefundLimit} />}
        <Field label={t.disclosureHi} value={c.ai_disclosure.hi} onChange={(v) => setC({ ...c, ai_disclosure: { ...c.ai_disclosure, hi: v } })} />
        <Field label={t.disclosureEn} value={c.ai_disclosure.en} onChange={(v) => setC({ ...c, ai_disclosure: { ...c.ai_disclosure, en: v } })} />
        <Field label={t.disclosureHinglish} value={c.ai_disclosure.hinglish} onChange={(v) => setC({ ...c, ai_disclosure: { ...c.ai_disclosure, hinglish: v } })} />
      </fieldset>
      {c.schema_version !== 2 && c.refund_auto_limit_paise != null && <small>{formatPaise(c.refund_auto_limit_paise)}</small>}
      {!locked && (
        <div className="row">
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
          <div className="card">
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
  );
}

export function PolicySettings() {
  const toast = useToast();
  const [list, reload] = useApi(() => api.get<Policy[]>("/policies"));
  const create = useSubmit(async () => {
    const latest = list.state === "data" ? list.data[0] : undefined;
    const base = latest?.content;
    await api.post<Policy>("/policies", {
      content: base ?? DEFAULT_POLICY,
      base_version: latest?.version ?? null,
    });
    reload();
  }, toast.error);
  return (
    <Screen title={t.policyTitle}>
      <button type="button" disabled={create.busy} onClick={() => void create.run()}>
        {t.policyNew}
      </button>
      <Async value={list} reload={reload}>
        {(items) => <>{items.map((p) => <Editor key={`${p.id}-${p.status}`} policy={p} onSaved={reload} />)}</>}
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
