import { useState } from "react";
import { api } from "../api/client";
import type { NotificationSettings } from "../api/types";
import { Async, Field, Screen, useSubmit } from "../components/common";
import { Icon } from "../components/Icons";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { t } from "../i18n";

function Form({ initial }: { initial: NotificationSettings }) {
  const toast = useToast();
  const [form, setForm] = useState(initial);
  const save = useSubmit(async () => {
    const body = {
      ...form,
      notify_email: form.notify_email?.trim() || null,
      alert_phone: form.alert_phone?.trim() || null,
      grievance_email: form.grievance_email?.trim() || null,
    };
    setForm(await api.put<NotificationSettings>("/settings/notifications", body));
    toast.info(t.saved);
  }, toast.error);

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void save.run();
      }}
    >
      <div className="card alert-card">
        <div className="alert-head">
          <span className="soft-icon">
            <Icon name="mail" className="xl" />
          </span>
          <div className="alert-title">
            <h2>{t.notifyEmailEnabled}</h2>
            <p>{t.notifyIntro}</p>
          </div>
          <button
            type="button"
            className={form.notify_email_enabled ? "switch on" : "switch"}
            aria-label="Toggle email alerts"
            onClick={() => setForm({ ...form, notify_email_enabled: !form.notify_email_enabled })}
          />
        </div>
        <Field label={t.notifyEmail} type="email" value={form.notify_email ?? ""} onChange={(v) => setForm({ ...form, notify_email: v })} />
        <div className="help">{t.notifyIntro}</div>
      </div>

      <div className="card alert-card">
        <div className="alert-head">
          <span className="soft-icon whatsapp-soft">
            <Icon name="whatsapp" className="xl" />
          </span>
          <div className="alert-title">
            <h2>{t.notifyWhatsappEnabled}</h2>
            <p>{t.notifyIntro}</p>
          </div>
          <button
            type="button"
            className={form.notify_whatsapp_enabled ? "switch on" : "switch"}
            aria-label="Toggle WhatsApp alerts"
            onClick={() => setForm({ ...form, notify_whatsapp_enabled: !form.notify_whatsapp_enabled })}
          />
        </div>
        <label className="field">
          <span className="field-label">{t.alertPhone}</span>
          <div className="phone-field">
            <span className="country">{"🇮🇳"}</span>
            <input className="input" value={form.alert_phone ?? ""} onChange={(e) => setForm({ ...form, alert_phone: e.target.value })} />
          </div>
        </label>
      </div>

      <div className="card alert-card">
        <div className="alert-head">
          <span className="soft-icon amber-soft">
            <Icon name="mail" className="xl" />
          </span>
          <div className="alert-title">
            <h2>{t.grievanceEmail}</h2>
            <p>Receive customer grievances and escalation emails.</p>
          </div>
        </div>
        <Field label={t.grievanceEmail} type="email" value={form.grievance_email ?? ""} onChange={(v) => setForm({ ...form, grievance_email: v })} />
      </div>

      <button type="submit" className="btn primary alert-save" disabled={save.busy}>
        <Icon name="file" />
        {t.save}
      </button>
    </form>
  );
}

export function NotificationsSettings() {
  const [data, reload] = useApi(() => api.get<NotificationSettings>("/settings/notifications"));
  return (
    <Screen title={t.navNotifications} subtitle="Choose how you receive important support and system notifications.">
      <Async value={data} reload={reload}>
        {(initial) => <Form initial={initial} />}
      </Async>
    </Screen>
  );
}
