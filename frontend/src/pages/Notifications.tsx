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
      className="card alert-card"
      onSubmit={(event) => {
        event.preventDefault();
        void save.run();
      }}
    >
      <div className="alert-head">
        <span className="soft-icon">
          <Icon name="bell" />
        </span>
        <div className="alert-title">
          <p>{t.notifyIntro}</p>
        </div>
      </div>
      <label className="row">
        <input type="checkbox" style={{ width: "auto" }} checked={form.notify_email_enabled} onChange={(e) => setForm({ ...form, notify_email_enabled: e.target.checked })} />
        {t.notifyEmailEnabled}
      </label>
      <Field label={t.notifyEmail} type="email" value={form.notify_email ?? ""} onChange={(v) => setForm({ ...form, notify_email: v })} />
      <label className="row">
        <input type="checkbox" style={{ width: "auto" }} checked={form.notify_whatsapp_enabled} onChange={(e) => setForm({ ...form, notify_whatsapp_enabled: e.target.checked })} />
        {t.notifyWhatsappEnabled}
      </label>
      <Field label={t.alertPhone} value={form.alert_phone ?? ""} onChange={(v) => setForm({ ...form, alert_phone: v })} />
      <Field label={t.grievanceEmail} type="email" value={form.grievance_email ?? ""} onChange={(v) => setForm({ ...form, grievance_email: v })} />
      <button type="submit" disabled={save.busy}>
        {t.save}
      </button>
    </form>
  );
}

export function NotificationsSettings() {
  const [data, reload] = useApi(() => api.get<NotificationSettings>("/settings/notifications"));
  return (
    <Screen title={t.navNotifications}>
      <Async value={data} reload={reload}>
        {(initial) => <Form initial={initial} />}
      </Async>
    </Screen>
  );
}
