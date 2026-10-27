import { useState } from "react";
import { api } from "../api/client";
import type { VoiceCall, VoiceCallDetail, VoiceSettings as Settings, VoiceStats } from "../api/types";
import { Async, Chip, Empty, Screen, StatCard } from "../components/common";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { formatIst } from "../format";
import { t, type Key } from "../i18n";

function SettingsStatus({ settings }: { settings: Settings }) {
  return (
    <section className="card">
      <p>{t.voiceIntro}</p>
      <p>
        <Chip tone={settings.configured ? "ok" : "warn"}>
          {settings.configured ? t.voiceStatusConfigured : t.voiceStatusNotConfigured}
        </Chip>{" "}
        {settings.configured && (
          <Chip tone={settings.enabled ? "ok" : "warn"}>
            {settings.enabled ? t.voiceEnabled : t.voiceStatusDisabled}
          </Chip>
        )}
      </p>
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
  return (
    <>
      <SettingsStatus settings={initial} />
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
        {(value) => <Body initial={value} />}
      </Async>
    </Screen>
  );
}
