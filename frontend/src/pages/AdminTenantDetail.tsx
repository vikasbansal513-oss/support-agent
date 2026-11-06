import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Quota, Tenant, TenantStats, VoiceSettings } from "../api/types";
import { Async, Chip, Field, ReasonDialog, Screen, StatCard } from "../components/common";
import { Icon } from "../components/Icons";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { formatIst } from "../format";
import { t, type Key } from "../i18n";

function VoiceSection({ tenantId }: { tenantId: string }) {
  const [settings, reload] = useApi(
    () => api.get<VoiceSettings>(`/admin/tenants/${tenantId}/voice-settings`),
    [tenantId],
  );
  return (
    <Async value={settings} reload={reload}>
      {(s) => <VoiceForm tenantId={tenantId} initial={s} onSaved={reload} />}
    </Async>
  );
}

function VoiceForm({
  tenantId,
  initial,
  onSaved,
}: {
  tenantId: string;
  initial: VoiceSettings;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [telephonyMode, setTelephonyMode] = useState(initial.telephony_mode);
  const [called, setCalled] = useState(initial.called_number ?? "");
  const [transfer, setTransfer] = useState(initial.transfer_number ?? "");
  const [enabled, setEnabled] = useState(initial.enabled || !initial.configured);
  const [provider, setProvider] = useState(initial.provider);
  const [rotate, setRotate] = useState(false);
  const [busy, setBusy] = useState(false);
  const [webhook, setWebhook] = useState<string | null>(null);

  const setMode = (mode: "ready_made" | "diy_plivo") => {
    setTelephonyMode(mode);
    setProvider(mode === "diy_plivo" ? "plivo" : initial.providers.find((p) => p !== "plivo") ?? "generic");
  };

  const save = async () => {
    setBusy(true);
    try {
      const out = await api.put<VoiceSettings>(`/admin/tenants/${tenantId}/voice-settings`, {
        called_number: called,
        transfer_number: transfer.trim() === "" ? null : transfer,
        enabled,
        rotate_webhook: rotate,
        provider,
        telephony_mode: telephonyMode,
      });
      setWebhook(out.webhook_url);
      setRotate(false);
      toast.info(t.saved);
      onSaved();
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card pad">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <label className="field">
          <span>{t.voiceTelephonyMode}</span>
          <select
            aria-label={t.voiceTelephonyMode}
            value={telephonyMode}
            onChange={(event) => setMode(event.target.value as "ready_made" | "diy_plivo")}
          >
            <option value="ready_made">{t.voiceModeReadyMade}</option>
            <option value="diy_plivo">{t.voiceModeDiyPlivo}</option>
          </select>
        </label>
        <Field label={t.voiceCalledNumber} value={called} onChange={setCalled} required />
        <Field label={t.voiceTransferNumber} value={transfer} onChange={setTransfer} />
        <label className="field">
          <span>{t.voiceProvider}</span>
          <select
            aria-label={t.voiceProvider}
            value={provider}
            disabled={telephonyMode === "diy_plivo"}
            onChange={(event) => setProvider(event.target.value)}
          >
            {(initial.providers.length ? initial.providers : [provider])
              .filter((name) => (telephonyMode === "diy_plivo" ? name === "plivo" : name !== "plivo"))
              .map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
          </select>
        </label>
        <label className="field">
          <span>{t.voiceEnabled}</span>
          <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
        </label>
        {initial.configured && (
          <label className="field">
            <span>{t.voiceRotate}</span>
            <input type="checkbox" checked={rotate} onChange={(event) => setRotate(event.target.checked)} />
          </label>
        )}
        {webhook && (
          <p role="status">
            {t.voiceWebhookOnce} <code>{webhook}</code>
          </p>
        )}
        <div className="row">
          <button type="submit" disabled={busy}>
            {t.voiceSave}
          </button>
        </div>
      </form>
    </section>
  );
}

type Tab = "overview" | "quota" | "voice" | "advanced";

function AdvancedTab({ tenant, reloadTenant }: { tenant: Tenant; reloadTenant: () => void }) {
  const toast = useToast();
  const [limitText, setLimitText] = useState(tenant.usage_override_limit === null ? "" : String(tenant.usage_override_limit));
  const [confirming, setConfirming] = useState<null | { kind: "limit" | "pause" | "resume" }>(null);
  const [busy, setBusy] = useState(false);

  const run = async (reason: string) => {
    if (!confirming) return;
    setBusy(true);
    try {
      if (confirming.kind === "limit")
        await api.put(`/admin/tenants/${tenant.id}/usage-limit`, { usage_override_limit: limitText.trim() === "" ? null : Number(limitText), reason });
      else await api.post(`/admin/tenants/${tenant.id}/${confirming.kind}`, { reason });
      setConfirming(null);
      reloadTenant();
      toast.info(t.saved);
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <section className="card pad">
        <h2 className="card-title">{t.advancedModeTitle}</h2>
        <p>
          {t.modeLabel}: {t[`mode_${tenant.mode}` as Key]}
        </p>
        <p className="help">{t.modeOwnerControlled}</p>
        <div className="row">
          {tenant.status === "active" && (
            <button type="button" className="secondary" disabled={busy} onClick={() => setConfirming({ kind: "pause" })}>
              <Icon name="pause" className="sm" /> {t.pauseTenant}
            </button>
          )}
          {tenant.status === "paused" && (
            <button type="button" disabled={busy} onClick={() => setConfirming({ kind: "resume" })}>
              {t.resumeTenant}
            </button>
          )}
        </div>
      </section>
      <section className="card pad">
        <h2 className="card-title">{t.advancedUsageLimitTitle}</h2>
        <label className="field">
          <span>{t.usageLimit}</span>
          <input
            aria-label={t.usageLimit}
            inputMode="numeric"
            placeholder={String(tenant.included_conversations)}
            value={limitText}
            onChange={(event) => setLimitText(event.target.value)}
          />
        </label>
        <button type="button" disabled={busy} onClick={() => setConfirming({ kind: "limit" })}>
          {t.save}
        </button>
      </section>
      {confirming && (
        <ReasonDialog title={`${tenant.name}: ${confirming.kind}`} onConfirm={(reason) => void run(reason)} onCancel={() => setConfirming(null)} />
      )}
    </>
  );
}

export function AdminTenantDetail() {
  const { id = "" } = useParams();
  const toast = useToast();
  const [tenant, reloadTenant] = useApi(() => api.get<Tenant>(`/admin/tenants/${id}`), [id]);
  const [stats, reloadStats] = useApi(() => api.get<TenantStats>(`/admin/tenants/${id}/stats`), [id]);
  const [quota, reloadQuota] = useApi(() => api.get<Quota>(`/admin/tenants/${id}/quota`), [id]);
  const [limitText, setLimitText] = useState("");
  const [warnText, setWarnText] = useState("");
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState<Tab>("overview");

  const saveQuota = async () => {
    setSaving(true);
    try {
      const limit = limitText.trim() === "" ? null : Number(limitText);
      const warn = warnText.trim() === "" ? 80 : Number(warnText);
      await api.put(`/admin/tenants/${id}/quota`, {
        monthly_call_limit: limit,
        warn_percent: warn,
        reason: "Admin updated quota",
      });
      reloadQuota();
      toast.info(t.saved);
    } catch (err) {
      toast.error(err);
    } finally {
      setSaving(false);
    }
  };

  const initials = (name: string) => {
    const parts = name.trim().split(/\s+/);
    return (parts[0]?.[0] ?? "").concat(parts.length > 1 ? parts[parts.length - 1]?.[0] ?? "" : "").toUpperCase() || "?";
  };

  return (
    <Screen title={t.brandDetailTitle}>
      <p><Link to="/admin/tenants">{t.backToBrands}</Link></p>
      <Async value={tenant} reload={reloadTenant}>
        {(tn) => (
          <>
            <section className="card pad brand-detail-head">
              <span className="initial">{initials(tn.name)}</span>
              <div className="brand-detail-who">
                <b>{tn.name}</b>
                <span>{tn.slug}</span>
              </div>
              <Chip tone={tn.status === "active" ? "ok" : "warn"}>{tn.status}</Chip>
              <Chip>{t.modeLabel}: {t[`mode_${tn.mode}` as Key]}</Chip>
              {tn.agent_paused && <Chip tone="urgent">{t.paused}</Chip>}
            </section>

            <div className="tabs">
              <button type="button" className={tab === "overview" ? "active" : ""} onClick={() => setTab("overview")}>
                {t.tabOverview}
              </button>
              <button type="button" className={tab === "quota" ? "active" : ""} onClick={() => setTab("quota")}>
                {t.tabQuota}
              </button>
              <button type="button" className={tab === "voice" ? "active" : ""} onClick={() => setTab("voice")}>
                {t.tabCallSettings}
              </button>
              <button type="button" className={tab === "advanced" ? "active" : ""} onClick={() => setTab("advanced")}>
                {t.tabAdvanced}
              </button>
            </div>

            {tab === "overview" && (
              <Async value={stats} reload={reloadStats}>
                {(s) => (
                  <div className="stats">
                    <StatCard label={t.messagesToday} value={s.messages_today} icon="chat" />
                    <StatCard label={t.monthlyCost} value={`₹${(s.monthly_cost_micro / 1_000_000).toFixed(2)}`} icon="file" />
                    <StatCard label={t.lastActivity} value={s.last_activity_at ? formatIst(s.last_activity_at) : "-"} icon="clock" />
                  </div>
                )}
              </Async>
            )}

            {tab === "quota" && (
              <Async value={quota} reload={reloadQuota}>
                {(q) => (
                  <section className="card pad">
                    <div className="stats">
                      <StatCard label={t.callsThisMonth} value={q.calls_this_month} icon="phone" />
                      <StatCard
                        label={t.usagePercent}
                        value={q.usage_percent === null ? t.noLimit : `${q.usage_percent.toFixed(1)}%`}
                        tone={q.usage_percent !== null && q.usage_percent >= 100 ? "warn" : undefined}
                        icon="info"
                      />
                    </div>
                    <label className="field">
                      <span>{t.monthlyLimit}</span>
                      <input
                        aria-label={t.monthlyLimit}
                        inputMode="numeric"
                        placeholder={q.monthly_call_limit === null ? t.noLimit : String(q.monthly_call_limit)}
                        value={limitText}
                        onChange={(event) => setLimitText(event.target.value)}
                      />
                    </label>
                    <label className="field">
                      <span>{t.warnPercent}</span>
                      <input
                        aria-label={t.warnPercent}
                        inputMode="numeric"
                        placeholder={String(q.warn_percent)}
                        value={warnText}
                        onChange={(event) => setWarnText(event.target.value)}
                      />
                    </label>
                    <button type="button" disabled={saving} onClick={() => void saveQuota()}>
                      {t.saveQuota}
                    </button>
                  </section>
                )}
              </Async>
            )}

            {tab === "voice" && <VoiceSection tenantId={id} />}

            {tab === "advanced" && <AdvancedTab tenant={tn} reloadTenant={reloadTenant} />}
          </>
        )}
      </Async>
    </Screen>
  );
}
