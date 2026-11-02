import { useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { Carrier, VoiceCall, VoiceCallDetail, VoiceSettings as Settings, VoiceStats } from "../api/types";
import { Async, Chip, Empty, Field, Screen } from "../components/common";
import { Icon } from "../components/Icons";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { formatIst } from "../format";
import { t, type Key } from "../i18n";

const CARRIERS: { value: Carrier; label: Key }[] = [
  { value: "jio", label: "voiceCarrierJio" },
  { value: "airtel", label: "voiceCarrierAirtel" },
  { value: "vi", label: "voiceCarrierVi" },
  { value: "bsnl", label: "voiceCarrierBsnl" },
  { value: "other", label: "voiceCarrierOther" },
];

function PublicNumberCard({
  settings,
  isOwner,
  onSaved,
}: {
  settings: Settings;
  isOwner: boolean;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [number, setNumber] = useState(settings.client_public_number ?? "");
  const [carrier, setCarrier] = useState<Carrier | "">(settings.carrier ?? "");
  const [busy, setBusy] = useState(false);
  const [confirmBusy, setConfirmBusy] = useState(false);
  const [showOff, setShowOff] = useState(false);
  const [copied, setCopied] = useState(false);

  if (!settings.configured) {
    return isOwner ? (
      <section className="card business-number">
        <div className="business-heading">
          <span className="soft-icon">
            <Icon name="phone" />
          </span>
          <div>
            <h2>{t.voicePublicNumberTitle}</h2>
            <p className="hint">{t.voicePublicNumberNeedsAiNumber}</p>
          </div>
        </div>
      </section>
    ) : null;
  }

  const save = async () => {
    setBusy(true);
    try {
      await api.put<Settings>("/voice/public-number", {
        client_public_number: number.trim() === "" ? null : number.trim(),
        carrier: carrier === "" ? null : carrier,
      });
      toast.info(t.voicePublicNumberSaved);
      onSaved();
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(false);
    }
  };

  const toggleConfirm = async (value: boolean) => {
    setConfirmBusy(true);
    try {
      await api.put<Settings>("/voice/public-number/confirm", { forwarding_confirmed: value });
      onSaved();
    } catch (err) {
      toast.error(err);
    } finally {
      setConfirmBusy(false);
    }
  };

  const copy = async (code: string) => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard may be unavailable (older browser/permissions) — code is still
      // shown on screen so they can note it down by hand.
    }
  };

  return (
    <section className="card business-number">
      <div className="business-heading">
        <span className="soft-icon">
          <Icon name="phone" />
        </span>
        <div>
          <h2>{t.voicePublicNumberTitle}</h2>
          <p>{t.voicePublicNumberIntro}</p>
        </div>
      </div>
      {isOwner ? (
        <>
          <Field label={t.voicePublicNumberLabel} value={number} onChange={setNumber} placeholder="9876543210" />
          <label className="field">
            <span>{t.voiceCarrierLabel}</span>
            <select value={carrier} onChange={(event) => setCarrier(event.target.value as Carrier | "")}>
              <option value="">—</option>
              {CARRIERS.map((c) => (
                <option key={c.value} value={c.value}>
                  {t[c.label]}
                </option>
              ))}
            </select>
          </label>
          <div className="row">
            <button type="button" disabled={busy} onClick={() => void save()}>
              {t.voicePublicNumberSave}
            </button>
          </div>
        </>
      ) : (
        settings.client_public_number && (
          <p>
            <code>{settings.client_public_number}</code>
          </p>
        )
      )}

      {settings.client_public_number && settings.forwarding_code && (
        <div className="banner">
          <p>{t.voiceForwardingHowTitle}</p>
          <div className="row">
            <code>{settings.forwarding_code}</code>
            <button type="button" className="secondary" onClick={() => void copy(settings.forwarding_code as string)}>
              {copied ? t.voiceForwardingCopied : t.voiceForwardingCopy}
            </button>
          </div>
          <small>{t.voiceForwardingNote}</small>
          {isOwner && (
            <div className="row">
              <label className="row">
                <input
                  type="checkbox"
                  style={{ width: "auto" }}
                  checked={settings.forwarding_confirmed}
                  disabled={confirmBusy}
                  onChange={(event) => void toggleConfirm(event.target.checked)}
                />
                {settings.forwarding_confirmed ? t.voiceForwardingConfirmed : t.voiceForwardingConfirm}
              </label>
            </div>
          )}
          <button type="button" className="secondary" onClick={() => setShowOff(!showOff)}>
            {t.voiceForwardingOffLink}
          </button>
          {showOff && settings.forwarding_off_code && (
            <p>
              {t.voiceForwardingOffTitle} <code>{settings.forwarding_off_code}</code>
            </p>
          )}
          <small className="hint">{t.voiceForwardingDisclaimer}</small>
        </div>
      )}

      {settings.client_public_number && !settings.forwarding_confirmed && (
        <p className="error-box">{t.voiceForwardingWarning}</p>
      )}
    </section>
  );
}

function SettingsStatus({ settings }: { settings: Settings }) {
  return (
    <section className="card calling-status">
      <div className="calling-intro">
        <span className="soft-icon">
          <Icon name="phone" />
        </span>
        <div>
          <p>{t.voiceIntro}</p>
        </div>
      </div>
      <div className="calling-badges">
        <Chip tone={settings.configured ? "ok" : "warn"}>
          {settings.configured ? t.voiceStatusConfigured : t.voiceStatusNotConfigured}
        </Chip>
        {settings.configured && (
          <Chip tone={settings.enabled ? "ok" : "warn"}>
            {settings.enabled ? t.voiceEnabled : t.voiceStatusDisabled}
          </Chip>
        )}
      </div>
      {settings.configured && (
        <p>
          {t.voiceCalledNumber}: <code>{settings.called_number}</code>
        </p>
      )}
      <p className="hint">{t.voiceReadOnlyNote}</p>
    </section>
  );
}

function CallDetail({ id, onBack }: { id: string; onBack: () => void }) {
  const toast = useToast();
  const [detail, reload] = useApi(() => api.get<VoiceCallDetail>(`/voice/calls/${id}`), [id]);
  const vote = async (verdict: "good" | "bad") => {
    try {
      await api.post<VoiceCall>(`/voice/calls/${id}/feedback`, { verdict });
      toast.info(t.voiceFeedbackSaved);
      reload();
    } catch (err) {
      toast.error(err);
    }
  };
  return (
    <div className="card recent-calls">
      <button type="button" className="secondary small" onClick={onBack}>
        {t.voiceBack}
      </button>
      <h2>{t.voiceTurns}</h2>
      <Async value={detail} reload={reload}>
        {(call) => (
          <>
          <p>
            {call.order_ref && (
              <>
                {t.voiceOrder} {call.order_ref} ·{" "}
              </>
            )}
            {call.summary_status && <Chip>{t[`voiceSummary_${call.summary_status}` as Key]}</Chip>}
          </p>
          <div className="row">
            <button type="button" aria-pressed={call.feedback === "good"} onClick={() => void vote("good")}>
              {t.voiceFeedbackGood}
            </button>
            <button type="button" aria-pressed={call.feedback === "bad"} onClick={() => void vote("bad")}>
              {t.voiceFeedbackBad}
            </button>
          </div>
          <ul className="transcript">
            {call.turns.map((turn, index) => (
              <li key={`${turn.at}-${index}`}>
                <strong>{turn.speaker === "caller" ? t.voiceCaller : t.voiceAgent}</strong>: {turn.text}
              </li>
            ))}
          </ul>
          </>
        )}
      </Async>
    </div>
  );
}

function Stats() {
  const [stats, reload] = useApi(() => api.get<VoiceStats>("/voice/stats?days=7"));
  return (
    <div className="card calling-analytics">
      <div className="calling-heading">
        <span className="soft-icon">
          <Icon name="review" />
        </span>
        <h2>{t.voiceStats}</h2>
      </div>
      <Async value={stats} reload={reload}>
        {(v) => (
          <div className="call-metrics">
            <div className="metric">
              <Icon name="phone" />
              <b>{v.calls}</b>
              <span>{t.voiceStatCalls}</span>
            </div>
            <div className="metric">
              <Icon name="check-circle" />
              <b>{v.agent_rate_percent}</b>
              <span>{t.voiceStatAgentRate}</span>
            </div>
            <div className="metric">
              <Icon name="arrows" />
              <b>{v.callback}</b>
              <span>{t.voiceStatCallback}</span>
            </div>
            <div className="metric">
              <Icon name="clock" />
              <b>{v.avg_latency_ms}</b>
              <span>{t.voiceStatLatency}</span>
            </div>
            <div className="metric">
              <Icon name="info" />
              <b>{v.slow_turns}</b>
              <span>{t.voiceStatSlow}</span>
            </div>
            <div className="metric">
              <Icon name="check-circle" />
              <b>{v.feedback_good}</b>
              <span>{t.voiceStatGood}</span>
            </div>
            <div className="metric">
              <Icon name="info" />
              <b>{v.feedback_bad}</b>
              <span>{t.voiceStatBad}</span>
            </div>
          </div>
        )}
      </Async>
    </div>
  );
}

function Calls() {
  const [calls, reload] = useApi(() => api.get<VoiceCall[]>("/voice/calls"));
  const [open, setOpen] = useState<string | null>(null);
  if (open) return <CallDetail id={open} onBack={() => setOpen(null)} />;
  return (
    <div className="card recent-calls">
      <div className="calling-heading">
        <span className="soft-icon">
          <Icon name="chat" />
        </span>
        <h2>{t.voiceCalls}</h2>
      </div>
      <Async value={calls} reload={reload}>
        {(rows) =>
          rows.length === 0 ? (
            <Empty>{t.voiceNoCalls}</Empty>
          ) : (
            <ul className="list">
              {rows.map((call) => (
                <li key={call.id}>
                  <button type="button" className="secondary small" onClick={() => setOpen(call.id)}>
                    {call.caller_masked ?? "-"} · {formatIst(call.started_at)}
                  </button>{" "}
                  <Chip tone={call.status === "callback" || call.status === "failed" ? "warn" : "ok"}>
                    {t[`voiceStatus_${call.status}` as Key]}
                  </Chip>
                </li>
              ))}
            </ul>
          )
        }
      </Async>
    </div>
  );
}

function Body({ initial, onSaved }: { initial: Settings; onSaved: () => void }) {
  const { user } = useAuth();
  const isOwner = user?.role === "owner";
  return (
    <>
      <div className="calling-top">
        <SettingsStatus settings={initial} />
        <PublicNumberCard settings={initial} isOwner={isOwner} onSaved={onSaved} />
      </div>
      {initial.configured && <Stats />}
      {initial.configured && <Calls />}
    </>
  );
}

export function VoiceSettings() {
  const [settings, reload] = useApi(() => api.get<Settings>("/voice/settings"));
  return (
    <Screen title={t.voiceTitle}>
      <Async value={settings} reload={reload}>
        {(value) => <Body initial={value} onSaved={reload} />}
      </Async>
    </Screen>
  );
}
