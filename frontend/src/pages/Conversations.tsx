import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type {
  Conversation,
  ConversationDetail,
  CustomerProfile as CustomerProfileData,
  EmailClassificationSummary,
  Message,
  Page,
} from "../api/types";
import { Async, Chip, Empty, ErrorBox, Screen, useSubmit } from "../components/common";
import { Icon } from "../components/Icons";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { formatIst } from "../format";
import { t, type Key } from "../i18n";

const STATUSES = ["", "open", "handoff", "resolved", "closed"] as const;
const CHANNEL_ICON: Record<Conversation["channel"], string> = { whatsapp: "whatsapp", email: "mail", voice: "phone" };
const EMAIL_ACTION_TONE: Record<EmailClassificationSummary["action"], "warn" | "ok" | "urgent" | undefined> = {
  PROCESS: "ok",
  SKIP: undefined,
  SKIP_DUPLICATE: undefined,
  MANUAL_REVIEW: "warn",
  QUARANTINE: "urgent",
};

export function Conversations() {
  const [status, setStatus] = useState<string>("");
  const [extra, setExtra] = useState<Conversation[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [first, reload] = useApi(async () => {
    const page = await api.get<Page<Conversation>>(`/conversations${status ? `?status=${status}` : ""}`);
    setExtra([]);
    setCursor(page.next_cursor);
    return page;
  }, [status]);
  const [loadError, setLoadError] = useState<Error | null>(null);

  const more = async () => {
    if (!cursor) return;
    try {
      const page = await api.get<Page<Conversation>>(
        `/conversations?${status ? `status=${status}&` : ""}cursor=${encodeURIComponent(cursor)}`,
      );
      setExtra((current) => [...current, ...page.items]);
      setCursor(page.next_cursor);
    } catch (err) {
      setLoadError(err instanceof Error ? err : new Error("unknown"));
    }
  };

  return (
    <Screen title={t.navConversations}>
      <div className="filters" role="tablist">
        {STATUSES.map((value) => (
          <button
            key={value || "all"}
            type="button"
            role="tab"
            aria-selected={value === status}
            className={value === status ? "filter-tab active" : "filter-tab"}
            onClick={() => setStatus(value)}
          >
            {value ? t[`status${value[0]?.toUpperCase()}${value.slice(1)}` as Key] : t.filterAll}
          </button>
        ))}
      </div>
      <Async value={first} reload={reload}>
        {(page) => {
          const items = [...page.items, ...extra];
          if (items.length === 0) return <Empty>{t.emptyConversations}</Empty>;
          return (
            <div className="card" style={{ padding: "14px 0" }}>
              {items.map((c) => {
                const title = c.customer.masked_phone ?? c.customer.masked_email ?? "-";
                return (
                  <Link key={c.id} to={`/conversations/${c.id}`} className="chat-row" style={{ textDecoration: "none", color: "inherit" }}>
                    <span className="initial">{title.slice(-2)}</span>
                    <span className="copy">
                      <b>{title}</b>
                      <p>
                        {c.active_order_ref ? `${c.active_order_ref} · ` : ""}
                        {formatIst(c.last_message_at)}
                      </p>
                      <span className="pill blue">
                        <Icon name={CHANNEL_ICON[c.channel]} className="sm" />
                        {c.channel}
                      </span>{" "}
                      <Chip tone={c.status === "handoff" ? "warn" : undefined}>{t[`status${c.status[0]?.toUpperCase()}${c.status.slice(1)}` as Key]}</Chip>
                      {c.human_active && <Chip tone="warn">Insaan</Chip>}
                    </span>
                  </Link>
                );
              })}
              {cursor && (
                <div style={{ padding: "12px 15px" }}>
                  <button type="button" className="secondary" onClick={() => void more()}>
                    {t.loadMore}
                  </button>
                </div>
              )}
              {loadError && (
                <div style={{ padding: "0 15px 12px" }}>
                  <ErrorBox error={loadError} onRetry={() => void more()} />
                </div>
              )}
            </div>
          );
        }}
      </Async>
    </Screen>
  );
}

function Bubble({ message, canReport, onReport }: { message: Message; canReport: boolean; onReport: (m: Message) => void }) {
  const unsent = message.direction === "out" && message.action_status !== null && message.action_status !== "sent";
  const className = message.direction === "in" ? "message" : "message out";
  const bubbleClassName = message.direction === "in" ? "message-bubble" : unsent ? "message-bubble unsent" : "message-bubble";
  return (
    <div className={className}>
      {message.direction === "in" && <span className="initial small">{message.sender.slice(0, 2).toUpperCase()}</span>}
      <div>
        <div className={bubbleClassName} style={unsent ? { background: "transparent", border: "1px dashed var(--blue)" } : undefined}>
          {message.content}
        </div>
        <span className="subline" style={message.direction === "out" ? { textAlign: "right" } : undefined}>
          {message.sender} · {formatIst(message.created_at)}
          {unsent && ` · ${t.notSent} (${message.action_status})`}
        </span>
        {message.email_classification && (
          <div>
            <Chip tone={EMAIL_ACTION_TONE[message.email_classification.action]}>
              {message.email_classification.category} · {message.email_classification.action}
            </Chip>
          </div>
        )}
        {canReport && message.sender === "agent" && !unsent && (
          <div>
            <button type="button" className="secondary small" onClick={() => onReport(message)}>
              {t.report}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export function ConversationView() {
  const { id = "" } = useParams();
  const toast = useToast();
  const [detail, reload] = useApi(() => api.get<ConversationDetail>(`/conversations/${id}`), [id]);
  const [text, setText] = useState("");
  const [reporting, setReporting] = useState<Message | null>(null);
  const [reason, setReason] = useState("");
  const [windowClosed, setWindowClosed] = useState(false);

  const send = useSubmit(async () => {
    try {
      await api.post<Message>(`/conversations/${id}/reply`, { content: text });
      setText("");
      reload();
    } catch (err) {
      if (err instanceof Error && "code" in err && (err as { code: string }).code === "E-ACT-002") setWindowClosed(true);
      throw err;
    }
  }, toast.error);

  const resolve = useSubmit(async () => {
    await api.post<Conversation>(`/conversations/${id}/resolve`);
    reload();
  }, toast.error);

  const report = useSubmit(async () => {
    if (!reporting) return;
    await api.post(`/messages/${reporting.id}/report`, { reason });
    toast.info(t.reportDone);
    setReporting(null);
    setReason("");
    reload();
  }, toast.error);

  return (
    <Screen title={t.navConversations}>
      <Async value={detail} reload={reload}>
        {(c) => (
          <div className="card chat-detail" style={{ minHeight: "calc(100vh - 220px)" }}>
            <div className="chat-person">
              <span className="initial">{(c.customer.masked_phone ?? c.customer.masked_email ?? "-").slice(-2)}</span>
              <div style={{ flex: 1 }}>
                <b>{c.customer.masked_phone ?? c.customer.masked_email ?? "-"}</b>
                <br />
                <span>
                  <Icon name={CHANNEL_ICON[c.channel]} className="sm" /> {c.channel}
                </span>
              </div>
              <Chip>{t[`status${c.status[0]?.toUpperCase()}${c.status.slice(1)}` as Key]}</Chip>
              <Link to={`/customers/${c.customer.id}`} className="secondary small" style={{ textDecoration: "none" }}>
                <Icon name="user" className="sm" /> {t.viewCustomerHistory}
              </Link>
              {c.status !== "resolved" && (
                <button type="button" className="secondary small" disabled={resolve.busy} onClick={() => void resolve.run()}>
                  {t.resolve}
                </button>
              )}
            </div>
            {c.human_active && <p className="banner">{t.humanActive}</p>}
            {windowClosed && <p className="banner">{t.windowClosed}</p>}
            <div className="messages">
              {c.messages.map((m) => (
                <Bubble key={m.id} message={m} canReport onReport={setReporting} />
              ))}
            </div>
            {reporting && (
              <div className="card">
                <label className="field">
                  <span className="field-label">{t.reportReason}</span>
                  <textarea className="textarea" value={reason} onChange={(event) => setReason(event.target.value)} />
                </label>
                <div className="row">
                  <button type="button" disabled={report.busy || reason.trim().length < 3} onClick={() => void report.run()}>
                    {t.report}
                  </button>
                  <button type="button" className="secondary" onClick={() => setReporting(null)}>
                    {t.cancel}
                  </button>
                </div>
              </div>
            )}
            <div className="chat-compose">
              <textarea placeholder={t.replyPlaceholder} value={text} maxLength={1500} onChange={(event) => setText(event.target.value)} />
              <div className="chat-compose-foot">
                <span />
                <button type="button" className="small" disabled={send.busy || text.trim() === "" || windowClosed} onClick={() => void send.run()}>
                  {t.send}
                </button>
              </div>
            </div>
          </div>
        )}
      </Async>
    </Screen>
  );
}

export function CustomerProfile() {
  const { id = "" } = useParams();
  const [profile, reload] = useApi(() => api.get<CustomerProfileData>(`/customers/${id}/profile`), [id]);

  return (
    <Screen title={t.customerProfileTitle}>
      <Async value={profile} reload={reload}>
        {(p) => {
          const title = p.masked_phone ?? p.masked_email ?? "-";
          return (
            <>
              <div className="card chat-person" style={{ marginBottom: 16 }}>
                <span className="initial">{title.slice(-2)}</span>
                <div style={{ flex: 1 }}>
                  <b>{title}</b>
                  <br />
                  {p.masked_phone && p.masked_email && (
                    <span>
                      <Icon name="mail" className="sm" /> {p.masked_email}
                    </span>
                  )}
                  <br />
                  <span className="subline">
                    {t.customerProfileSince} {formatIst(p.created_at)}
                  </span>
                </div>
              </div>

              <h3>{t.customerProfileConversationsTitle}</h3>
              {p.conversations.length === 0 ? (
                <Empty>{t.emptyCustomerProfileConversations}</Empty>
              ) : (
                <div className="card" style={{ padding: "14px 0", marginBottom: 16 }}>
                  {p.conversations.map((c) => (
                    <Link key={c.id} to={`/conversations/${c.id}`} className="chat-row" style={{ textDecoration: "none", color: "inherit" }}>
                      <span className="pill blue">
                        <Icon name={CHANNEL_ICON[c.channel]} className="sm" />
                        {c.channel}
                      </span>
                      <span className="copy">
                        <Chip tone={c.status === "handoff" ? "warn" : undefined}>
                          {t[`status${c.status[0]?.toUpperCase()}${c.status.slice(1)}` as Key] ?? c.status}
                        </Chip>
                        <p>{formatIst(c.last_message_at ?? c.created_at)}</p>
                      </span>
                    </Link>
                  ))}
                </div>
              )}

              <h3>{t.customerProfileHandoffsTitle}</h3>
              {p.handoffs.length === 0 ? (
                <Empty>{t.emptyCustomerProfileHandoffs}</Empty>
              ) : (
                <div className="card" style={{ padding: "14px 0", marginBottom: 16 }}>
                  {p.handoffs.map((h) => (
                    <Link key={h.id} to={`/conversations/${h.conversation_id}`} className="chat-row" style={{ textDecoration: "none", color: "inherit" }}>
                      <span className="pill blue">
                        <Icon name={CHANNEL_ICON[h.channel]} className="sm" />
                        {h.channel}
                      </span>
                      <span className="copy">
                        <b>{h.reason_code}</b>
                        <p>
                          {h.order_ref ? `${h.order_ref} · ` : ""}
                          {formatIst(h.created_at)}
                        </p>
                        <Chip tone={h.priority === "urgent" ? "urgent" : undefined}>{h.priority}</Chip>{" "}
                        <Chip tone={h.status === "open" ? "warn" : h.status === "claimed" ? "ok" : undefined}>{h.status}</Chip>
                      </span>
                    </Link>
                  ))}
                </div>
              )}

              <h3>{t.customerProfileActionsTitle}</h3>
              {p.actions.length === 0 ? (
                <Empty>{t.emptyCustomerProfileActions}</Empty>
              ) : (
                <div className="card" style={{ padding: "14px 0" }}>
                  {p.actions.map((a) => (
                    <Link key={a.id} to={`/conversations/${a.conversation_id}`} className="chat-row" style={{ textDecoration: "none", color: "inherit" }}>
                      <span className="pill blue">
                        <Icon name={CHANNEL_ICON[a.channel]} className="sm" />
                        {a.channel}
                      </span>
                      <span className="copy">
                        <b>{a.tool_name ?? "-"}</b>
                        <p>
                          {a.order_ref ? `${a.order_ref} · ` : ""}
                          {a.amount_paise != null ? `₹${(a.amount_paise / 100).toFixed(2)} · ` : ""}
                          {formatIst(a.created_at)}
                        </p>
                        <Chip>{a.status}</Chip>
                      </span>
                    </Link>
                  ))}
                </div>
              )}
            </>
          );
        }}
      </Async>
    </Screen>
  );
}
