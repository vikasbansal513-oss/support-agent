import { api } from "../api/client";
import type { AgentStatus, DashboardStats, Usage } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Async, Chip, Empty, Screen, StatCard, useSubmit } from "../components/common";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { t, type Key } from "../i18n";

export function Dashboard() {
  const { user } = useAuth();
  const toast = useToast();
  const [stats, reloadStats] = useApi(() => api.get<DashboardStats>("/dashboard/stats"));
  const [usage] = useApi(() => api.get<Usage>("/usage"));
  const [status, reloadStatus] = useApi(() => api.get<AgentStatus>("/agent/status"));
  const owner = user?.role === "owner";

  const toggle = (paused: boolean) =>
    useSubmit(async () => {
      await api.post<AgentStatus>(paused ? "/agent/pause" : "/agent/resume", {
        reason: paused ? "Dashboard se roka gaya" : "Dashboard se chalu kiya gaya",
      });
      reloadStatus();
      reloadStats();
    }, toast.error);

  const pause = toggle(true);
  const resume = toggle(false);

  return (
    <Screen title={t.navDashboard}>
      <Async value={stats} reload={reloadStats}>
        {(s) => (
          <>
            <div className="row">
              <Chip tone={s.mode === "auto" ? "ok" : "warn"}>
                {t.modeLabel}: {t[`mode_${s.mode}` as Key]}
              </Chip>
              {s.agent_paused && <Chip tone="urgent">{t.paused}</Chip>}
              {owner && (
                <button type="button" className="secondary" disabled={pause.busy || resume.busy} onClick={() => void (s.agent_paused ? resume.run() : pause.run())}>
                  {s.agent_paused ? t.resumeAgent : t.pauseAgent}
                </button>
              )}
            </div>
            {s.messages_today === 0 && s.open_handoffs === 0 && <Empty>{t.emptyDashboard}</Empty>}
            <div className="grid">
              <StatCard label={t.messagesToday} value={s.messages_today} />
              <StatCard label={t.handledToday} value={s.handled_by_agent_today} tone="ok" />
              <StatCard label={t.handedOffToday} value={s.handed_off_today} />
              <StatCard label={t.openHandoffs} value={s.open_handoffs} tone={s.open_handoffs > 0 ? "warn" : undefined} />
            </div>
            {s.mode === "shadow" && (
              <div className="grid">
                <StatCard label={t.shadowCorrect} value={s.shadow_correct} tone="ok" />
                <StatCard label={t.shadowWrong} value={s.shadow_wrong} tone={s.shadow_wrong > 0 ? "warn" : undefined} />
                <StatCard label={t.shadowUnreviewed} value={s.shadow_unreviewed} />
              </div>
            )}
          </>
        )}
      </Async>
      <Async value={usage}>
        {(u) => {
          const percent = u.limit > 0 ? Math.min(100, Math.floor((u.conversation_count * 100) / u.limit)) : 100;
          return (
            <div className="card">
              <h2>{t.usageTitle}</h2>
              <p>
                {u.conversation_count} / {u.limit}
              </p>
              <div className={u.limit_reached ? "bar full" : "bar"} role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
                <div style={{ width: `${percent}%` }} />
              </div>
              {u.limit_reached && <p className="banner">{t.usageReached}</p>}
            </div>
          );
        }}
      </Async>
      <Async value={status}>
        {(a) =>
          a.integration_errors.length > 0 ? (
            <p className="banner">Integration me dikkat: {a.integration_errors.join(", ")}</p>
          ) : (
            <></>
          )
        }
      </Async>
    </Screen>
  );
}
