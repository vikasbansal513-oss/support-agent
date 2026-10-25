import { useState } from "react";
import { api } from "../api/client";
import type { Level, Limits, PolicyContent, Policy, Regression, WriteTool } from "../api/types";
import { Async, Field, Screen } from "../components/common";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { formatPaise, rupeesToPaise } from "../format";
import { t, type Key } from "../i18n";

const TOOLS: WriteTool[] = ["address_update", "order_cancel", "exchange_request", "refund_create"];
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

function Editor({ policy, latest, onDone }: { policy: Policy; latest: number; onDone: () => void }) {
  const toast = useToast();
  const c = policy.content;
  const [levels, setLevels] = useState<Record<WriteTool, Level>>({
    address_update: c.autonomy?.address_update ?? "off",
    order_cancel: c.autonomy?.order_cancel ?? "off",
    exchange_request: c.autonomy?.exchange_request ?? "off",
    refund_create: c.autonomy?.refund_create ?? "off",
  });
  const [limits, setLimits] = useState<Record<keyof Limits, string>>(() => {
    const base = c.limits ?? DEFAULT_LIMITS;
    return Object.fromEntries(LIMIT_KEYS.map((k) => [k, String(base[k] / 100)])) as Record<keyof Limits, string>;
  });
  const [busy, setBusy] = useState(false);

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

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <p>{t.autonomyIntro}</p>
      {TOOLS.map((tool) => {
        const current = c.autonomy?.[tool] ?? "off";
        return (
          <label key={tool} className="field">
            <span>{t[`tool_${tool}` as Key]}</span>
            {current === "auto" ? (
              <strong>{t.autonomyAutoByAdmin}</strong>
            ) : (
              <select
                aria-label={t[`tool_${tool}` as Key]}
                value={levels[tool]}
                onChange={(event) => setLevels({ ...levels, [tool]: event.target.value as Level })}
              >
                <option value="off">{t.level_off}</option>
                <option value="approval">{t.level_approval}</option>
              </select>
            )}
          </label>
        );
      })}
      <h2>{t.limitsTitle}</h2>
      {LIMIT_KEYS.map((k) => (
        <Field
          key={k}
          label={t[`limit_${k}` as Key]}
          value={limits[k]}
          onChange={(value) => setLimits({ ...limits, [k]: value })}
        />
      ))}
      <small>{formatPaise((c.limits ?? DEFAULT_LIMITS).refund_auto_limit_paise)}</small>
      <div className="row">
        <button type="submit" disabled={busy}>
          {t.autonomySave}
        </button>
      </div>
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
    <div>
      <p>{t.autonomyNeedsV2}</p>
      <button type="button" disabled={busy} onClick={() => void run()}>
        {t.autonomyUpgrade}
      </button>
    </div>
  );
}

export function AutonomySettings() {
  const [list, reload] = useApi(() => api.get<Policy[]>("/policies"));
  return (
    <Screen title={t.autonomyTitle}>
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
