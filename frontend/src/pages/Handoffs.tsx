import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Handoff, Page } from "../api/types";
import { Async, Chip, Empty, Screen, useSubmit } from "../components/common";
import { Icon } from "../components/Icons";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { formatIst, minutesLeft } from "../format";
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

type DetailTab = "overview" | "conversation" | "actions" | "customer";

function HandoffRow({ item, selected, onSelect }: { item: Handoff; selected: boolean; onSelect: () => void }) {
  return (
    <button type="button" className={selected ? "handoff-row selected" : "handoff-row"} onClick={onSelect}>
      <span className={`channel-icon ${item.priority === "urgent" ? "purple" : "blue"}`}>
        <Icon name={item.priority === "urgent" ? "info" : "chat"} />
      </span>
      <span className="handoff-copy">
        <b>{item.summary.customer_masked_phone ?? item.summary.order_ref ?? item.id}</b>
        <span>{item.summary.customer_asked}</span>
        <small>
          {item.reason_code} &middot; {item.summary.order_ref ?? "-"}
        </small>
      </span>
      <span className="handoff-meta">
        <span>{formatIst(item.created_at)}</span>
        <Chip tone={item.priority === "urgent" ? "urgent" : undefined}>{item.priority === "urgent" ? "High" : "Medium"}</Chip>
        <Chip tone={item.status === "open" ? "warn" : item.status === "claimed" ? "ok" : undefined}>
          {item.status === "open" ? t.statusOpen : item.status === "claimed" ? t.claimed : t.statusResolved}
        </Chip>
      </span>
    </button>
  );
}

function HandoffDetail({ item, onChange }: { item: Handoff; onChange: () => void }) {
  const toast = useToast();
  const [tab, setTab] = useState<DetailTab>("overview");
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
    <div className="card handoff-detail" style={{ padding: 0, overflow: "hidden" }}>
      <div className="handoff-detail-head">
        <span className={`channel-icon ${item.priority === "urgent" ? "purple" : "blue"}`}>
          <Icon name={item.priority === "urgent" ? "info" : "chat"} />
        </span>
        <div className="handoff-who">
          <b>{item.summary.customer_masked_phone ?? item.summary.order_ref ?? item.id}</b>
          <span>
            {item.summary.customer_masked_phone ?? "-"} &middot; {item.reason_code}
          </span>
        </div>
        <div className="head-actions">
          <SlaTimer due={item.sla_due_at} breached={item.sla_breached} />
          {item.priority === "urgent" && <Chip tone="urgent">{t.urgent}</Chip>}
          {item.status === "claimed" && <Chip tone="ok">{t.claimed}</Chip>}
          <Link to={`/conversations/${item.conversation_id}`} className="btn small">
            {t.navConversations}
          </Link>
          {item.status === "open" && (
            <button type="button" className="btn primary small" disabled={claim.busy} onClick={() => void claim.run()}>
              {t.claim}
            </button>
          )}
          <button type="button" className="btn small" disabled={resolve.busy} onClick={() => void resolve.run()}>
            {t.resolve}
          </button>
        </div>
      </div>
      <div className="tabs">
        <button type="button" className={tab === "overview" ? "active" : ""} onClick={() => setTab("overview")}>
          Overview
        </button>
        <button type="button" className={tab === "conversation" ? "active" : ""} onClick={() => setTab("conversation")}>
          Conversation
        </button>
        <button type="button" className={tab === "actions" ? "active" : ""} onClick={() => setTab("actions")}>
          AI Actions
        </button>
        <button type="button" className={tab === "customer" ? "active" : ""} onClick={() => setTab("customer")}>
          Customer Info
        </button>
      </div>

      {tab === "overview" && (
        <div className="handoff-summary">
          <div className="ai-summary">
            <div className="ai-title">{t.aiSummaryTitle}</div>
            <div className="ai-body">
              <div className="summary-item">
                <div>
                  <b>{t.customerSaid}:</b>
                  <p>{item.summary.customer_asked}</p>
                </div>
              </div>
              {item.summary.agent_did.length > 0 && (
                <div className="summary-item">
                  <div>
                    <b>{t.aiActionsTaken}:</b>
                    <p>{item.summary.agent_did.join("; ")}</p>
                  </div>
                </div>
              )}
              <div className="summary-item">
                <div>
                  <b>{t.whyHandoffTitle}:</b>
                  <p>{item.summary.why_stuck}</p>
                </div>
              </div>
              <div className="required-action">
                <div>
                  <b>Required Human Action:</b>
                  <p>{item.summary.why_stuck}</p>
                </div>
              </div>
            </div>
          </div>
          <div className="details-card">
            <h3>
              <Icon name="info" />
              {t.handoffDetailsTitle}
            </h3>
            <div className="detail-line">
              <span>{t.orderLabel}</span>
              <b>{item.summary.order_ref ?? "-"}</b>
            </div>
            <div className="detail-line">
              <span>{t.ageLabel}</span>
              <b>
                <Icon name="calendar" />
                {formatIst(item.created_at)}
              </b>
            </div>
            <div className="detail-line">
              <span>{t.statusLabel}</span>
              <b>
                <Chip tone={item.status === "open" ? "warn" : item.status === "claimed" ? "ok" : undefined}>{item.status}</Chip>
              </b>
            </div>
            <div className="detail-line">
              <span>Priority</span>
              <b>
                <Chip tone={item.priority === "urgent" ? "urgent" : undefined}>{item.priority}</Chip>
              </b>
            </div>
          </div>
        </div>
      )}

      {tab === "conversation" && (
        <div style={{ padding: "15px 18px" }}>
          <p>
            Full conversation history is available on the <Link to={`/conversations/${item.conversation_id}`}>Chats</Link> page.
          </p>
        </div>
      )}

      {tab === "actions" && (
        <div style={{ padding: "15px 18px" }}>
          {item.summary.agent_did.length === 0 ? (
            <Empty>No AI actions recorded for this handoff.</Empty>
          ) : (
            <ul className="list">
              {item.summary.agent_did.map((action, index) => (
                <li key={index}>{action}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {tab === "customer" && (
        <div style={{ padding: "15px 18px" }}>
          <div className="detail-line">
            <span>Phone / email</span>
            <b>{item.summary.customer_masked_phone ?? "-"}</b>
          </div>
          <div className="detail-line">
            <span>{t.orderLabel}</span>
            <b>{item.summary.order_ref ?? "-"}</b>
          </div>
          <div className="detail-line">
            <span>Language</span>
            <b>{item.summary.language}</b>
          </div>
        </div>
      )}
    </div>
  );
}

export function Handoffs() {
  const [list, reload] = useApi(() => api.get<Page<Handoff>>("/handoffs"));
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "open" | "claimed" | "resolved">("all");
  const [priorityFilter, setPriorityFilter] = useState<"all" | "urgent" | "normal">("all");
  const [selected, setSelected] = useState<string | null>(null);

  return (
    <Screen
      title={t.navHandoffs}
      subtitle="Customer conversations that need human assistance, with AI summary and full context."
      actions={
        <button type="button" className="btn outline-blue">
          <Icon name="settings" />
          Handoff Settings
        </button>
      }
    >
      <Async value={list} reload={reload}>
        {(page) => {
          const items = page.items;
          const open = items.filter((h) => h.status === "open").length;
          const claimed = items.filter((h) => h.status === "claimed").length;
          const resolved = items.filter((h) => h.status === "resolved").length;

          const filtered = items.filter((h) => {
            if (statusFilter !== "all" && h.status !== statusFilter) return false;
            if (priorityFilter !== "all" && h.priority !== priorityFilter) return false;
            if (!search.trim()) return true;
            return [h.summary.customer_asked, h.summary.customer_masked_phone, h.summary.order_ref, h.reason_code]
              .filter(Boolean)
              .join(" ")
              .toLowerCase()
              .includes(search.trim().toLowerCase());
          });
          const current = filtered.find((h) => h.id === selected) ?? filtered[0] ?? null;

          if (items.length === 0) return <Empty>{t.emptyHandoffs}</Empty>;

          return (
            <>
              <div className="stats">
                <StatCardInline icon="users" label={t.handoffsTotal} value={items.length} />
                <StatCardInline icon="clock" label={t.statusOpen} value={open} tone={open > 0 ? "warn" : undefined} />
                <StatCardInline icon="user" label={t.claimed} value={claimed} />
                <StatCardInline icon="check-circle" label={t.statusResolved} value={resolved} tone="ok" />
              </div>
              <div className="filters">
                <div className="filter-right" style={{ marginLeft: 0, width: "100%", flexWrap: "wrap" }}>
                  <select className="select" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as typeof statusFilter)}>
                    <option value="all">Status: All</option>
                    <option value="open">{t.statusOpen}</option>
                    <option value="claimed">{t.claimed}</option>
                    <option value="resolved">{t.statusResolved}</option>
                  </select>
                  <select className="select" value={priorityFilter} onChange={(e) => setPriorityFilter(e.target.value as typeof priorityFilter)}>
                    <option value="all">Priority: All</option>
                    <option value="urgent">High</option>
                    <option value="normal">Medium</option>
                  </select>
                  <input
                    className="search"
                    style={{ flex: 1, minWidth: 220 }}
                    placeholder={t.searchHandoffsPlaceholder}
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </div>
              </div>
              {filtered.length === 0 ? (
                <Empty>{t.emptyHandoffs}</Empty>
              ) : (
                <div className="handoff-grid" style={{ display: "grid", gridTemplateColumns: "minmax(300px,.85fr) minmax(430px,1.3fr)", gap: 11 }}>
                  <div className="card" style={{ padding: "6px 0", overflow: "hidden" }}>
                    {filtered.map((h) => (
                      <HandoffRow key={h.id} item={h} selected={current?.id === h.id} onSelect={() => setSelected(h.id)} />
                    ))}
                  </div>
                  {current && <HandoffDetail item={current} onChange={reload} />}
                </div>
              )}
            </>
          );
        }}
      </Async>
    </Screen>
  );
}

function StatCardInline({ icon, label, value, tone }: { icon: string; label: string; value: number; tone?: "warn" | "ok" }) {
  const toneClass = tone === "ok" ? "green" : tone === "warn" ? "orange" : "blue";
  return (
    <div className="stat-card">
      <div className={`stat-icon ${toneClass}`}>
        <Icon name={icon} />
      </div>
      <div>
        <div className="stat-number">{value}</div>
        <div className="stat-label">{label}</div>
      </div>
    </div>
  );
}
