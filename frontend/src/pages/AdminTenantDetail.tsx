import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Quota, Tenant, TenantStats } from "../api/types";
import { Async, Chip, Screen, StatCard } from "../components/common";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { formatIst } from "../format";
import { t } from "../i18n";

export function AdminTenantDetail() {
  const { id = "" } = useParams();
  const toast = useToast();
  const [tenant, reloadTenant] = useApi(() => api.get<Tenant>(`/admin/tenants/${id}`), [id]);
  const [stats, reloadStats] = useApi(() => api.get<TenantStats>(`/admin/tenants/${id}/stats`), [id]);
  const [quota, reloadQuota] = useApi(() => api.get<Quota>(`/admin/tenants/${id}/quota`), [id]);
  const [limitText, setLimitText] = useState("");
  const [warnText, setWarnText] = useState("");
  const [saving, setSaving] = useState(false);

  const saveQuota = async () => {
    setSaving(true);
    try {
      const limit = limitText.trim() === "" ? null : Number(limitText);
      const warn = warnText.trim() === "" ? 80 : Number(warnText);
      await api.put(`/admin/tenants/${id}/quota`, {
        monthly_call_limit: limit,
        warn_percent: warn,
        reason: "Admin ne quota update kiya",
      });
      reloadQuota();
      toast.info(t.saved);
    } catch (err) {
      toast.error(err);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Screen title={t.brandDetailTitle}>
      <p><Link to="/admin/tenants">{t.backToBrands}</Link></p>
      <Async value={tenant} reload={reloadTenant}>
        {(tn) => (
          <section className="card">
            <h2>{tn.name}</h2>
            <p>
              <Chip tone={tn.status === "active" ? "ok" : "warn"}>{tn.status}</Chip>{" "}
              <Chip>{t.modeLabel}: {tn.mode}</Chip>
            </p>
          </section>
        )}
      </Async>
      <Async value={stats} reload={reloadStats}>
        {(s) => (
          <div className="grid">
            <StatCard label={t.messagesToday} value={s.messages_today} />
            <StatCard label={t.monthlyCost} value={`₹${(s.monthly_cost_micro / 1_000_000).toFixed(2)}`} />
            <StatCard label={t.lastActivity} value={s.last_activity_at ? formatIst(s.last_activity_at) : "-"} />
          </div>
        )}
      </Async>
      <h2>{t.quotaTitle}</h2>
      <Async value={quota} reload={reloadQuota}>
        {(q) => (
          <section className="card">
            <div className="grid">
              <StatCard label={t.callsThisMonth} value={q.calls_this_month} />
              <StatCard
                label={t.usagePercent}
                value={q.usage_percent === null ? t.noLimit : `${q.usage_percent.toFixed(1)}%`}
                tone={q.usage_percent !== null && q.usage_percent >= 100 ? "warn" : undefined}
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
    </Screen>
  );
}
