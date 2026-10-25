import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Handoff, Page } from "../api/types";
import { Async, Chip, Empty, Screen, useSubmit } from "../components/common";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { minutesLeft } from "../format";
import { t } from "../i18n";

export function SlaTimer({ due, breached }: { due: string | null; breached: boolean }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(timer);
  }, []);
  const left = minutesLeft(due, now);
  if (left === null) return null;
  if (breached || left < 0) return <Chip tone="urgent">{t.slaBreached}</Chip>;
  return (
    <Chip tone={left < 10 ? "warn" : undefined}>
      {left} {t.slaLeft}
    </Chip>
  );
}

function HandoffCard({ item, onChange }: { item: Handoff; onChange: () => void }) {
  const toast = useToast();
  const claim = useSubmit(async () => {
    await api.post<Handoff>(`/handoffs/${item.id}/claim`);
    onChange();
  }, (err) => {
    toast.error(err);
    onChange();
  });
  const resolve = useSubmit(async () => {
    await api.post<Handoff>(`/handoffs/${item.id}/resolve`);
    onChange();
  }, toast.error);
  return (
    <div className="card">
      <div className="row">
        {item.priority === "urgent" && <Chip tone="urgent">{t.urgent}</Chip>}
        <Chip>{item.reason_code}</Chip>
        <SlaTimer due={item.sla_due_at} breached={item.sla_breached} />
        {item.status === "claimed" && <Chip tone="ok">{t.claimed}</Chip>}
      </div>
      <p>{item.summary.customer_asked}</p>
      <small>
        {item.summary.customer_masked_phone ?? "-"} · {item.summary.order_ref ?? "-"} · {item.summary.why_stuck}
      </small>
      <div className="row">
        <Link to={`/conversations/${item.conversation_id}`}>{t.navConversations}</Link>
        {item.status === "open" && (
          <button type="button" disabled={claim.busy} onClick={() => void claim.run()}>
            {t.claim}
          </button>
        )}
        <button type="button" className="secondary" disabled={resolve.busy} onClick={() => void resolve.run()}>
          {t.resolve}
        </button>
      </div>
    </div>
  );
}

export function Handoffs() {
  const [list, reload] = useApi(() => api.get<Page<Handoff>>("/handoffs"));
  return (
    <Screen title={t.navHandoffs}>
      <Async value={list} reload={reload}>
        {(page) =>
          page.items.length === 0 ? <Empty>{t.emptyHandoffs}</Empty> : <>{page.items.map((h) => <HandoffCard key={h.id} item={h} onChange={reload} />)}</>
        }
      </Async>
    </Screen>
  );
}
