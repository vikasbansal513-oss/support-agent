import { useState } from "react";
import { api } from "../api/client";
import type { GmailCheckNow, GmailConnect, Integration, IntegrationKind, ProbeResult } from "../api/types";
import { t } from "../i18n";
import { Chip, Field, useSubmit } from "./common";
import { useToast } from "./Toast";

const LABEL: Record<IntegrationKind, string> = {
  shopify: "Shopify",
  gupshup: "WhatsApp (Gupshup)",
  gmail: "Gmail",
  shiprocket: "Shiprocket",
  delhivery: "Delhivery",
  bluedart: "Blue Dart",
  dtdc: "DTDC",
  ecom_express: "Ecom Express",
  xpressbees: "XpressBees",
  shadowfax: "Shadowfax",
};

export const COURIER_KINDS: IntegrationKind[] = [
  "shiprocket",
  "delhivery",
  "bluedart",
  "dtdc",
  "ecom_express",
  "xpressbees",
  "shadowfax",
];

const COURIER_HINT: Partial<Record<IntegrationKind, string>> = {
  delhivery: t.courierHintDelhivery,
  shiprocket: t.courierHintShiprocket,
};

const LOGO_CLASS: Partial<Record<IntegrationKind, string>> = {
  shopify: "shopify",
  gupshup: "whatsapp",
  gmail: "gmail",
  shiprocket: "shiprocket",
};
const LOGO_CHAR: Record<IntegrationKind, string> = {
  shopify: "S",
  gupshup: "W",
  gmail: "G",
  shiprocket: "SR",
  delhivery: "D",
  bluedart: "BD",
  dtdc: "DT",
  ecom_express: "EE",
  xpressbees: "XB",
  shadowfax: "SF",
};

function IntegrationCard({ kind, item, onChange }: { kind: IntegrationKind; item: Integration | undefined; onChange: () => void }) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const [c, setC] = useState("");
  const [d, setD] = useState("");
  const [webhook, setWebhook] = useState<string | null>(null);
  const [probe, setProbe] = useState<ProbeResult | null>(null);

  const save = useSubmit(async () => {
    let path = `/integrations/${kind}`;
    let body: Record<string, string>;
    if (kind === "shopify") body = { shop_domain: a.trim(), access_token: b };
    else if (kind === "gupshup") body = { userid: a, password: b, source_number: c.trim(), app_name: d };
    else body = { credential: a };
    const saved = await api.put<Integration>(path, body);
    setWebhook(saved.webhook_url);
    setEditing(false);
    setA("");
    setB("");
    setC("");
    setD("");
    toast.info(t.saved);
    onChange();
  }, toast.error);

  const test = useSubmit(async () => {
    const result = await api.post<ProbeResult>(`/integrations/${kind}/test`);
    setProbe(result);
    onChange();
  }, toast.error);

  const connectGmail = useSubmit(async () => {
    const out = await api.post<GmailConnect>("/integrations/gmail/connect");
    window.location.assign(out.url);
  }, toast.error);

  const disconnectGmail = useSubmit(async () => {
    if (!window.confirm(t.gmailDisconnectConfirm)) return;
    await api.delete<Integration>("/integrations/gmail");
    setProbe(null);
    toast.info(t.gmailDisconnectedToast);
    onChange();
  }, toast.error);

  const checkNowGmail = useSubmit(async () => {
    await api.post<GmailCheckNow>("/integrations/gmail/check-now");
    toast.info(t.gmailCheckNowQueuedToast);
    onChange();
  }, toast.error);

  const editable = kind !== "gmail";
  const configured = item?.configured ?? false;
  return (
    <div className="card integration-card">
      <div className="integration-row">
        <span className={`integration-logo ${LOGO_CLASS[kind] ?? ""}`}>{LOGO_CHAR[kind]}</span>
        <div className="integration-main">
          <h2>
            {LABEL[kind]}
            <Chip tone={item?.status === "ok" ? "ok" : item?.status === "auth_error" ? "urgent" : undefined}>{item?.status ?? "not_configured"}</Chip>
          </h2>
          {kind === "gmail" && configured && item?.config.address && (
            <p>
              {t.gmailConnectedAs}: {item.config.address}
            </p>
          )}
          {kind === "gmail" && item?.status === "auth_error" && <p className="banner">{t.gmailAuthError}</p>}
          {kind !== "gmail" && configured && item?.secret_hint && (
            <p>
              {t.secretSet}: {item.secret_hint}
            </p>
          )}
          {item?.last_error_code && <p className="banner">{item.last_error_code}</p>}
          {webhook && (
            <div className="banner">
              <strong>{t.webhookUrl}</strong>
              <p style={{ wordBreak: "break-all" }}>{webhook}</p>
              <button type="button" className="small" onClick={() => void navigator.clipboard.writeText(webhook)}>
                {t.copy}
              </button>
            </div>
          )}
          {probe && <p>{probe.ok ? t.testOk : `${probe.error_code ?? ""} ${probe.detail ?? ""}`}</p>}
          {editing && editable && (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void save.run();
              }}
            >
              {kind === "shopify" && (
                <>
                  <Field label={t.shopDomain} value={a} onChange={setA} required />
                  <Field label={t.accessToken} type="password" value={b} onChange={setB} autoComplete="off" required />
                </>
              )}
              {kind === "gupshup" && (
                <>
                  <Field label={t.gupshupUser} value={a} onChange={setA} required />
                  <Field label={t.gupshupPassword} type="password" value={b} onChange={setB} autoComplete="off" required />
                  <Field label={t.sourceNumber} value={c} onChange={setC} required />
                  <Field label={t.gupshupAppName} value={d} onChange={setD} />
                </>
              )}
              {COURIER_KINDS.includes(kind) && (
                <Field
                  label={`${t.courierCredential} (${COURIER_HINT[kind] ?? t.courierHintGeneric})`}
                  type="password"
                  value={a}
                  onChange={setA}
                  autoComplete="off"
                  required
                />
              )}
              <small>{t.secretWriteOnly}</small>
              <div className="row">
                <button type="submit" className="small" disabled={save.busy}>
                  {t.save}
                </button>
                <button type="button" className="secondary small" onClick={() => setEditing(false)}>
                  {t.cancel}
                </button>
              </div>
            </form>
          )}
          {!editing && (
            <div className="integration-actions">
              {editable && (
                <button type="button" className="secondary small" onClick={() => setEditing(true)}>
                  {configured ? t.editCredentials : LABEL[kind]}
                </button>
              )}
              {kind === "gmail" && (
                <button type="button" className="small" disabled={connectGmail.busy} onClick={() => void connectGmail.run()}>
                  {configured ? t.gmailReconnect : t.gmailConnect}
                </button>
              )}
              {kind === "gmail" && configured && (
                <button type="button" className="secondary small" disabled={disconnectGmail.busy} onClick={() => void disconnectGmail.run()}>
                  {t.gmailDisconnect}
                </button>
              )}
              {kind === "gmail" && configured && (
                <button type="button" className="small" disabled={checkNowGmail.busy} onClick={() => void checkNowGmail.run()}>
                  {t.gmailCheckNow}
                </button>
              )}
              {configured && (
                <button type="button" className="small" disabled={test.busy} onClick={() => void test.run()}>
                  {t.testConnection}
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function LogisticsSection({ items, onChange }: { items: Integration[]; onChange: () => void }) {
  const configuredFirst = COURIER_KINDS.find((kind) => items.find((i) => i.kind === kind)?.configured);
  const [selected, setSelected] = useState<IntegrationKind>(configuredFirst ?? "shiprocket");
  return (
    <div className="card pad">
      <h2 className="card-title">{t.logisticsHeading}</h2>
      <label className="field">
        <span className="field-label">{t.logisticsProvider}</span>
        <select value={selected} onChange={(event) => setSelected(event.target.value as IntegrationKind)}>
          {COURIER_KINDS.map((kind) => {
            const configured = items.find((i) => i.kind === kind)?.configured ?? false;
            return (
              <option key={kind} value={kind}>
                {LABEL[kind]}
                {configured ? ` — ${t.logisticsConfigured}` : ""}
              </option>
            );
          })}
        </select>
      </label>
      <div className="nested-provider">
        <IntegrationCard key={selected} kind={selected} item={items.find((i) => i.kind === selected)} onChange={onChange} />
      </div>
    </div>
  );
}

export function IntegrationsPanel({ items, onChange }: { items: Integration[]; onChange: () => void }) {
  return (
    <>
      <IntegrationCard kind="shopify" item={items.find((i) => i.kind === "shopify")} onChange={onChange} />
      <IntegrationCard kind="gupshup" item={items.find((i) => i.kind === "gupshup")} onChange={onChange} />
      <LogisticsSection items={items} onChange={onChange} />
      <IntegrationCard kind="gmail" item={items.find((i) => i.kind === "gmail")} onChange={onChange} />
    </>
  );
}
