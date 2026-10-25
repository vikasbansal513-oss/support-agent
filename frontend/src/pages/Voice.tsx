import { useState } from "react";
import { api } from "../api/client";
import type { VoiceCall, VoiceCallDetail, VoiceSettings as Settings, VoiceStats } from "../api/types";
import { Async, Chip, Empty, Field, Screen, StatCard } from "../components/common";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { formatIst } from "../format";
import { t, type Key } from "../i18n";

function SettingsForm({ initial, configured, onSaved }: { initial: Settings; configured: boolean; onSaved: (out: Settings) => void }) {
  const toast = useToast();
  const [called, setCalled] = useState(initial.called_number ?? "");
  const [transfer, setTransfer] = useState(initial.transfer_number ?? "");
  const [enabled, setEnabled] = useState(initial.enabled || !initial.configured);
  const [provider, setProvider] = useState(initial.provider);
  const [rotate, setRotate] = useState(false);
  const [busy, setBusy] = useState(false);
  const [webhook, setWebhook] = useState<string | null>(null);

  const save = async () => {
    setBusy(true);
    try {
      const out = await api.put<Settings>("/voice/settings", {
        called_number: called,
        transfer_number: transfer.trim() === "" ? null : transfer,
        enabled,
        rotate_webhook: rotate,
        provider,
      });
      setWebhook(out.webhook_url);
      setRotate(false);
      toast.info(t.voiceSaved);
      onSaved(out);
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <p>{t.voiceIntro}</p>
      <Field label={t.voiceCalledNumber} value={called} onChange={setCalled} required />
      <Field label={t.voiceTransferNumber} value={transfer} onChange={setTransfer} />
      <label className="field">
        <span>{t.voiceProvider}</span>
        <select aria-label={t.voiceProvider} value={provider} onChange={(event) => setProvider(event.target.value)}>
          {(initial.providers.length ? initial.providers : [provider]).map((name) => (
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
      {configured && (
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
    <div>
      <button type="button" onClick={onBack}>
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
    <div>
      <h2>{t.voiceStats}</h2>
      <Async value={stats} reload={reload}>
        {(v) => (
          <div className="stats">
            <StatCard label={t.voiceStatCalls} value={v.calls} />
            <StatCard label={t.voiceStatAgentRate} value={v.agent_rate_percent} tone="ok" />
            <StatCard label={t.voiceStatCallback} value={v.callback} tone={v.callback ? "warn" : undefined} />
            <StatCard label={t.voiceStatLatency} value={v.avg_latency_ms} />
            <StatCard label={t.voiceStatSlow} value={v.slow_turns} tone={v.slow_turns ? "warn" : undefined} />
            <StatCard label={t.voiceStatGood} value={v.feedback_good} />
            <StatCard label={t.voiceStatBad} value={v.feedback_bad} />
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
    <div>
      <h2>{t.voiceCalls}</h2>
      <Async value={calls} reload={reload}>
        {(rows) =>
          rows.length === 0 ? (
            <Empty>{t.voiceNoCalls}</Empty>
          ) : (
            <ul className="list">
              {rows.map((call) => (
                <li key={call.id}>
                  <button type="button" onClick={() => setOpen(call.id)}>
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

function Body({ initial }: { initial: Settings }) {
  const [configured, setConfigured] = useState(initial.configured);
  return (
    <>
      <SettingsForm initial={initial} configured={configured} onSaved={(out) => setConfigured(out.configured)} />
      {configured && <Stats />}
      {configured && <Calls />}
    </>
  );
}

export function VoiceSettings() {
  const [settings, reload] = useApi(() => api.get<Settings>("/voice/settings"));
  return (
    <Screen title={t.voiceTitle}>
      <Async value={settings} reload={reload}>
        {(value) => <Body initial={value} />}
      </Async>
    </Screen>
  );
}
