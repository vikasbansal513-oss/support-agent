import { useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { DashboardStats, Level, Limits, Mode, PolicyContent, Policy, Regression, WriteTool } from "../api/types";
import { Async, Field, ReasonDialog, Screen } from "../components/common";
import { Icon } from "../components/Icons";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { formatPaise, rupeesToPaise } from "../format";
import { t, type Key } from "../i18n";

const TOOLS: WriteTool[] = ["address_update", "order_cancel", "exchange_request", "return_request", "refund_create"];
const LIMIT_KEYS: (keyof Limits)[] = [
  "cancel_auto_limit_paise",
  "exchange_auto_limit_paise",
  "refund_auto_limit_paise",
  "refund_customer_daily_paise",
  "brand_daily_auto_value_paise",
  "high_value_threshold_paise",
];
const DEFAULT_LIMITS: Limits = {
  cancel_auto_limit_paise: 200_000,
  exchange_auto_limit_paise: 200_000,
  refund_auto_limit_paise: 50_000,
  refund_customer_daily_paise: 100_000,
  brand_daily_auto_value_paise: 2_000_000,
  high_value_threshold_paise: 500_000,
};
const READ_TOOLS = ["handoff_to_human", "order_lookup", "tracking_lookup"];

async function publishContent(content: PolicyContent, baseVersion: number): Promise<void> {
  const draft = await api.post<Policy>("/policies", { content, base_version: baseVersion });
  await api.get<Regression>(`/policies/${draft.id}/regression`);
  await api.post<Policy>(`/policies/${draft.id}/publish`);
}

function AutoConfirmModal({
  tool,
  onClose,
  onDone,
}: {
  tool: WriteTool;
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  const confirm = async () => {
    setBusy(true);
    try {
      await api.put("/policies/autonomy", {
        tool,
        level: "auto",
        reason: reason.trim() || t[`tool_${tool}` as Key],
        confirmed: true,
      });
      toast.info(t.autonomyAutoChanged);
      onDone();
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="dialog" role="dialog" aria-modal="true">
      <div className="card pad">
        <h2 className="card-title">{t.autonomyAutoConfirmTitle}</h2>
        <p>{t.autonomyAutoConfirmBody}</p>
        <Field label={t.autonomyAutoConfirmReason} value={reason} onChange={setReason} placeholder={t[`tool_${tool}` as Key]} />
        <div className="row">
          <button type="button" disabled={busy || reason.trim().length < 5} onClick={() => void confirm()}>
            {t.autonomyAutoConfirmGo}
          </button>
          <button type="button" className="secondary" disabled={busy} onClick={onClose}>
            {t.cancel}
          </button>
        </div>
      </div>
    </div>
  );
}

function Editor({ policy, latest, onDone }: { policy: Policy; latest: number; onDone: () => void }) {
  const toast = useToast();
  const { user } = useAuth();
  const isOwner = user?.role === "owner";
  const c = policy.content;
  const [levels, setLevels] = useState<Record<WriteTool, Level>>({
    address_update: c.autonomy?.address_update ?? "off",
    order_cancel: c.autonomy?.order_cancel ?? "off",
    exchange_request: c.autonomy?.exchange_request ?? "off",
    return_request: c.autonomy?.return_request ?? "off",
    refund_create: c.autonomy?.refund_create ?? "off",
  });
  const [limits, setLimits] = useState<Record<keyof Limits, string>>(() => {
    const base = c.limits ?? DEFAULT_LIMITS;
    return Object.fromEntries(LIMIT_KEYS.map((k) => [k, String(base[k] / 100)])) as Record<keyof Limits, string>;
  });
  const [busy, setBusy] = useState(false);
  const [autoTarget, setAutoTarget] = useState<WriteTool | null>(null);
  const [turningOff, setTurningOff] = useState<WriteTool | null>(null);

  const save = async () => {
    setBusy(true);
    try {
      const nextLimits = Object.fromEntries(LIMIT_KEYS.map((k) => [k, rupeesToPaise(limits[k])])) as unknown as Limits;
      const enabled = [...READ_TOOLS, ...TOOLS.filter((tool) => levels[tool] !== "off")].sort();
      const next = {
        ...c,
        autonomy: { order_status: "auto", ...levels },
        limits: nextLimits,
        enabled_tools: enabled,
      } as unknown as PolicyContent;
      await publishContent(next, latest);
      toast.info(t.autonomyDone);
      onDone();
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(false);
    }
  };

  const turnOffAuto = async (tool: WriteTool) => {
    setTurningOff(tool);
    try {
      await api.put("/policies/autonomy", {
        tool,
        level: "approval",
        reason: t[`tool_${tool}` as Key],
        confirmed: false,
      });
      toast.info(t.autonomyAutoChanged);
      onDone();
    } catch (err) {
      toast.error(err);
    } finally {
      setTurningOff(null);
    }
  };

  return (
    <form
      className="autonomy-layout"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <div className="card autonomy-permissions">
        <p>{isOwner ? t.autonomyIntro : t.autonomyIntroStaff}</p>
        {TOOLS.map((tool) => {
          const current = c.autonomy?.[tool] ?? "off";
          if (current === "auto") {
            return (
              <div key={tool} className="permission-row">
                <div>
                  <b>{t[`tool_${tool}` as Key]}</b>
                  <span>{t.autonomyAutoByAdmin}</span>
                </div>
                {isOwner && (
                  <button type="button" className="secondary small" disabled={turningOff === tool} onClick={() => void turnOffAuto(tool)}>
                    {t.level_approval}
                  </button>
                )}
              </div>
            );
          }
          return (
            <div key={tool} className="permission-row">
              <div>
                <b>{t[`tool_${tool}` as Key]}</b>
              </div>
              <div className="segmented" role="group" aria-hidden="true">
                <button type="button" className={levels[tool] === "off" ? "chosen" : ""} onClick={() => setLevels({ ...levels, [tool]: "off" })}>
                  {t.level_off}
                </button>
                <button type="button" className={levels[tool] === "approval" ? "chosen" : ""} onClick={() => setLevels({ ...levels, [tool]: "approval" })}>
                  {t.level_approval}
                </button>
                {isOwner && (
                  <button type="button" className={levels[tool] === "auto" ? "chosen" : ""} onClick={() => setAutoTarget(tool)}>
                    {t.level_auto}
                  </button>
                )}
              </div>
              <select
                aria-label={t[`tool_${tool}` as Key]}
                className="sr-only"
                value={levels[tool]}
                onChange={(event) => {
                  const value = event.target.value as Level;
                  if (value === "auto") {
                    setAutoTarget(tool);
                    return;
                  }
                  setLevels({ ...levels, [tool]: value });
                }}
              >
                <option value="off">{t.level_off}</option>
                <option value="approval">{t.level_approval}</option>
                {isOwner && <option value="auto">{t.level_auto}</option>}
              </select>
            </div>
          );
        })}
        <h2 className="card-title" style={{ marginTop: 18 }}>{t.limitsTitle}</h2>
        <div className="limit-grid">
          {LIMIT_KEYS.map((k) => (
            <Field
              key={k}
              label={t[`limit_${k}` as Key]}
              value={limits[k]}
              onChange={(value) => setLimits({ ...limits, [k]: value })}
            />
          ))}
        </div>
        <small className="help">{formatPaise((c.limits ?? DEFAULT_LIMITS).refund_auto_limit_paise)}</small>
        <div className="row autonomy-actions" style={{ marginTop: 16 }}>
          <button type="submit" disabled={busy}>
            {t.autonomySave}
          </button>
        </div>
      </div>
      <div className="card autonomy-help">
        <h2>
          <Icon name="info" /> <span>{t.navAutonomy}</span>
        </h2>
        <div className="how-item">
          <i>1</i>
          <div>
            <b>{t.level_off}</b>
            <p>{t.autonomyIntroStaff}</p>
          </div>
        </div>
        <div className="how-item">
          <i>2</i>
          <div>
            <b>{t.level_approval}</b>
            <p>{t.autonomyAutoConfirmBody}</p>
          </div>
        </div>
        <div className="how-item">
          <i className="green-num">3</i>
          <div>
            <b>{t.level_auto}</b>
            <p>{t.autonomyAutoByAdmin}</p>
          </div>
        </div>
      </div>
      {autoTarget && (
        <AutoConfirmModal
          tool={autoTarget}
          onClose={() => setAutoTarget(null)}
          onDone={() => {
            setAutoTarget(null);
            onDone();
          }}
        />
      )}
    </form>
  );
}

function Upgrade({ policy, latest, onDone }: { policy: Policy; latest: number; onDone: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      const { refund_auto_limit_paise: _a, refund_customer_daily_limit_paise: _b, enabled_tools: _e, ...rest } =
        policy.content as PolicyContent & { enabled_tools?: string[] };
      void _a;
      void _b;
      void _e;
      const next = { ...rest, schema_version: 2, enabled_tools: READ_TOOLS } as unknown as PolicyContent;
      await publishContent(next, latest);
      onDone();
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="card pad">
      <p>{t.autonomyNeedsV2}</p>
      <button type="button" disabled={busy} onClick={() => void run()}>
        {t.autonomyUpgrade}
      </button>
    </div>
  );
}

function ModeEditor({ initialMode, isOwner, onDone }: { initialMode: Mode; isOwner: boolean; onDone: () => void }) {
  const toast = useToast();
  const [mode, setMode] = useState<Mode>(initialMode);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const save = async (reason: string) => {
    setBusy(true);
    try {
      await api.put("/settings/mode", { mode, reason });
      toast.info(t.autonomyDone);
      setConfirming(false);
      onDone();
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card autonomy-permissions">
      <h2 className="card-title">{t.modeLabel}</h2>
      <p>{t.modeHelp}</p>
      {isOwner ? (
        <>
          <div className="segmented" role="group" aria-hidden="true">
            {(["shadow", "draft", "auto"] as Mode[]).map((m) => (
              <button key={m} type="button" className={mode === m ? "chosen" : ""} onClick={() => setMode(m)}>
                {t[`mode_${m}` as Key]}
              </button>
            ))}
          </div>
          <select
            aria-label={t.modeLabel}
            className="sr-only"
            value={mode}
            onChange={(event) => setMode(event.target.value as Mode)}
          >
            {(["shadow", "draft", "auto"] as Mode[]).map((m) => (
              <option key={m} value={m}>
                {t[`mode_${m}` as Key]}
              </option>
            ))}
          </select>
          <div className="row" style={{ marginTop: 12 }}>
            <button type="button" disabled={busy || mode === initialMode} onClick={() => setConfirming(true)}>
              {t.save}
            </button>
          </div>
        </>
      ) : (
        <p>{t[`mode_${initialMode}` as Key]}</p>
      )}
      {confirming && (
        <ReasonDialog title={t.modeLabel} onConfirm={(reason) => void save(reason)} onCancel={() => setConfirming(false)} />
      )}
    </div>
  );
}

export function AutonomySettings() {
  const { user } = useAuth();
  const isOwner = user?.role === "owner";
  const [list, reload] = useApi(() => api.get<Policy[]>("/policies"));
  const [stats, reloadStats] = useApi(() => api.get<DashboardStats>("/dashboard/stats"));
  return (
    <Screen title={t.autonomyTitle}>
      <Async value={stats} reload={reloadStats}>
        {(s) => <ModeEditor key={s.mode} initialMode={s.mode} isOwner={isOwner} onDone={reloadStats} />}
      </Async>
      <Async value={list} reload={reload}>
        {(policies) => {
          const published = policies.find((p) => p.status === "published");
          const latest = Math.max(0, ...policies.map((p) => p.version));
          if (!published) return <p>{t.noRows}</p>;
          return published.content.schema_version === 2 ? (
            <Editor key={published.id} policy={published} latest={latest} onDone={reload} />
          ) : (
            <Upgrade policy={published} latest={latest} onDone={reload} />
          );
        }}
      </Async>
    </Screen>
  );
}
