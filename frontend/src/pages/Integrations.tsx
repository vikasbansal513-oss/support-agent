import { Fragment, useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Integration, Policy } from "../api/types";
import { Async, Chip, Screen } from "../components/common";
import { Icon } from "../components/Icons";
import { COURIER_KINDS, IntegrationsPanel } from "../components/IntegrationsPanel";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { t } from "../i18n";

export function IntegrationsSettings() {
  const [list, reload] = useApi(() => api.get<Integration[]>("/integrations"));
  const [params, setParams] = useSearchParams();
  const toast = useToast();
  const gmail = params.get("gmail");
  useEffect(() => {
    if (!gmail) return;
    if (gmail === "connected") toast.info(t.gmailConnectedToast);
    else toast.info(t.gmailConnectFailed);
    setParams({}, { replace: true });
  }, [gmail]);
  return (
    <Screen title={t.navIntegrations}>
      <Async value={list} reload={reload}>
        {(items) => <IntegrationsPanel items={items} onChange={reload} />}
      </Async>
    </Screen>
  );
}

export function Onboarding() {
  const [list, reload] = useApi(() => api.get<Integration[]>("/integrations"));
  const [policies, reloadPolicies] = useApi(() => api.get<Policy[]>("/policies"));
  const refresh = () => {
    reload();
    reloadPolicies();
  };
  return (
    <Screen title={t.onboardingTitle}>
      <Async value={list} reload={reload}>
        {(items) => {
          const ok = (kind: string) => items.some((i) => i.kind === kind && i.configured && i.status === "ok");
          const courier = COURIER_KINDS.some(
            (kind) => items.some((i) => i.kind === kind && i.configured),
          );
          const published = policies.state === "data" && policies.data.some((p) => p.status === "published");
          const steps: [string, boolean][] = [
            [t.stepShopify, ok("shopify")],
            [t.stepGupshup, items.some((i) => i.kind === "gupshup" && i.configured)],
            [t.stepCourier, courier],
            [t.stepPolicy, published],
          ];
          const doneCount = steps.filter(([, done]) => done).length;
          return (
            <>
              <div className="card setup-checklist">
                <div className="setup-top">
                  <div className="privacy-heading">
                    <span className="soft-icon">
                      <Icon name="check-square" />
                    </span>
                    <div>
                      <p>
                        {doneCount}/{steps.length}
                      </p>
                    </div>
                  </div>
                  <div className="progress-label">
                    <div className="bar-progress">
                      <span style={{ width: `${(doneCount / steps.length) * 100}%` }} />
                    </div>
                    <b>
                      {doneCount}/{steps.length}
                    </b>
                  </div>
                </div>
                <ol className="setup-steps">
                  {steps.map(([label, done], index) => (
                    <Fragment key={label}>
                      <li className={done ? "setup-step completed" : "setup-step"}>
                        <span className={done ? "step-check" : "step-check hollow"}>{done ? "✓" : index + 1}</span>
                        <span>
                          <b>{label}</b>
                          <Chip tone={done ? "ok" : "warn"}>{done ? t.stepDone : t.stepPending}</Chip>
                        </span>
                      </li>
                      {index < steps.length - 1 && <span className="step-arrow">›</span>}
                    </Fragment>
                  ))}
                </ol>
              </div>
              <IntegrationsPanel items={items} onChange={refresh} />
              <Link to="/settings/policy">{t.navPolicy}</Link>
            </>
          );
        }}
      </Async>
    </Screen>
  );
}
