import { useState } from "react";
import { api } from "../api/client";
import type { Integration, IntegrationKind, ProbeResult } from "../api/types";
import { t } from "../i18n";
import { Chip, Field, useSubmit } from "./common";
import { useToast } from "./Toast";

const LABEL: Record<IntegrationKind, string> = {
  shopify: "Shopify",
  gupshup: "WhatsApp (Gupshup)",
  gmail: "Gmail",
  shiprocket: "Shiprocket",
  delhivery: "Delhivery",
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

  const editable = kind !== "gmail";
  const configured = item?.configured ?? false;
  return (
    <div className="card">
      <div className="row">
        <h2>{LABEL[kind]}</h2>
        <Chip tone={item?.status === "ok" ? "ok" : item?.status === "auth_error" ? "urgent" : undefined}>{item?.status ?? "not_configured"}</Chip>
      </div>
      {configured && item?.secret_hint && (
        <p>
          {t.secretSet}: {item.secret_hint}
        </p>
      )}
      {item?.last_error_code && <p className="banner">{item.last_error_code}</p>}
      {webhook && (
        <div className="banner">
          <strong>{t.webhookUrl}</strong>
          <p style={{ wordBreak: "break-all" }}>{webhook}</p>
          <button type="button" onClick={() => void navigator.clipboard.writeText(webhook)}>
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
          {(kind === "shiprocket" || kind === "delhivery") && (
            <Field
              label={`${t.courierCredential} (${kind === "delhivery" ? t.courierHintDelhivery : t.courierHintShiprocket})`}
              type="password"
              value={a}
              onChange={setA}
              autoComplete="off"
              required
            />
          )}
          <small>{t.secretWriteOnly}</small>
          <div className="row">
            <button type="submit" disabled={save.busy}>
              {t.save}
            </button>
            <button type="button" className="secondary" onClick={() => setEditing(false)}>
              {t.cancel}
            </button>
          </div>
        </form>
      )}
      {!editing && (
        <div className="row">
          {editable && (
            <button type="button" className="secondary" onClick={() => setEditing(true)}>
              {configured ? t.save : LABEL[kind]}
            </button>
          )}
          {configured && (
            <button type="button" disabled={test.busy} onClick={() => void test.run()}>
              {t.testConnection}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export function IntegrationsPanel({ items, onChange }: { items: Integration[]; onChange: () => void }) {
  const kinds: IntegrationKind[] = ["shopify", "gupshup", "shiprocket", "delhivery", "gmail"];
  return (
    <>
      {kinds.map((kind) => (
        <IntegrationCard key={kind} kind={kind} item={items.find((i) => i.kind === kind)} onChange={onChange} />
      ))}
    </>
  );
}
