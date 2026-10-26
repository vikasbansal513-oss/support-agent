import { useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Integration, Policy } from "../api/types";
import { Async, Chip, Screen } from "../components/common";
import { IntegrationsPanel } from "../components/IntegrationsPanel";
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
          const courier = ok("shiprocket") || ok("delhivery");
          const published = policies.state === "data" && policies.data.some((p) => p.status === "published");
          const steps: [string, boolean][] = [
            [t.stepShopify, ok("shopify")],
            [t.stepGupshup, items.some((i) => i.kind === "gupshup" && i.configured)],
            [t.stepCourier, courier],
            [t.stepPolicy, published],
          ];
          return (
            <>
              <ol className="card">
                {steps.map(([label, done]) => (
                  <li key={label}>
                    {label} <Chip tone={done ? "ok" : "warn"}>{done ? t.stepDone : t.stepPending}</Chip>
                  </li>
                ))}
              </ol>
              <IntegrationsPanel items={items} onChange={refresh} />
              <Link to="/settings/policy">{t.navPolicy}</Link>
            </>
          );
        }}
      </Async>
    </Screen>
  );
}
