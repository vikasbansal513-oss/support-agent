import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Conversation, ConversationDetail, Message, Page } from "../api/types";
import { Async, Chip, Empty, ErrorBox, Screen, useSubmit } from "../components/common";
import { useToast } from "../components/Toast";
import { useApi } from "../components/useApi";
import { formatIst } from "../format";
import { t, type Key } from "../i18n";

const STATUSES = ["", "open", "handoff", "resolved", "closed"] as const;

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
      <div className="row" role="tablist">
        {STATUSES.map((value) => (
          <button key={value || "all"} type="button" className={value === status ? "" : "secondary"} onClick={() => setStatus(value)}>
            {value ? t[`status${value[0]?.toUpperCase()}${value.slice(1)}` as Key] : t.filterAll}
          </button>
        ))}
      </div>
      <Async value={first} reload={reload}>
        {(page) => {
          const items = [...page.items, ...extra];
          if (items.length === 0) return <Empty>{t.emptyConversations}</Empty>;
          return (
            <>
              {items.map((c) => (
                <Link key={c.id} to={`/conversations/${c.id}`} className="card" style={{ display: "block", color: "inherit", textDecoration: "none" }}>
                  <div className="row">
                    <strong>{c.customer.masked_phone ?? c.customer.masked_email ?? "-"}</strong>
                    <Chip tone={c.status === "handoff" ? "warn" : undefined}>{t[`status${c.status[0]?.toUpperCase()}${c.status.slice(1)}` as Key]}</Chip>
                    {c.human_active && <Chip tone="warn">Insaan</Chip>}
                    {c.active_order_ref && <Chip>{c.active_order_ref}</Chip>}
                  </div>
                  <small>{formatIst(c.last_message_at)}</small>
                </Link>
              ))}
              {cursor && (
                <button type="button" className="secondary" onClick={() => void more()}>
                  {t.loadMore}
                </button>
              )}
              {loadError && <ErrorBox error={loadError} onRetry={() => void more()} />}
            </>
          );
        }}
      </Async>
    </Screen>
  );
}

function Bubble({ message, canReport, onReport }: { message: Message; canReport: boolean; onReport: (m: Message) => void }) {
  const unsent = message.direction === "out" && message.action_status !== null && message.action_status !== "sent";
  const className = message.direction === "in" ? "bubble bubble-in" : unsent ? "bubble bubble-out unsent" : "bubble bubble-out";
  return (
    <div className={className}>
      {message.content}
      <small>
        {message.sender} · {formatIst(message.created_at)}
        {unsent && ` · ${t.notSent} (${message.action_status})`}
      </small>
      {canReport && message.sender === "agent" && !unsent && (
        <button type="button" className="secondary" onClick={() => onReport(message)}>
          {t.report}
        </button>
      )}
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
          <>
            <div className="row">
              <strong>{c.customer.masked_phone ?? c.customer.masked_email ?? "-"}</strong>
              <Chip>{c.channel}</Chip>
              <Chip>{t[`status${c.status[0]?.toUpperCase()}${c.status.slice(1)}` as Key]}</Chip>
              {c.status !== "resolved" && (
                <button type="button" className="secondary" disabled={resolve.busy} onClick={() => void resolve.run()}>
                  {t.resolve}
                </button>
              )}
            </div>
            {c.human_active && <p className="banner">{t.humanActive}</p>}
            {windowClosed && <p className="banner">{t.windowClosed}</p>}
            <div>
              {c.messages.map((m) => (
                <Bubble key={m.id} message={m} canReport onReport={setReporting} />
              ))}
            </div>
            {reporting && (
              <div className="card">
                <label className="field">
                  <span>{t.reportReason}</span>
                  <textarea value={reason} onChange={(event) => setReason(event.target.value)} />
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
            <div className="sticky-actions">
              <label className="field">
                <textarea placeholder={t.replyPlaceholder} value={text} maxLength={1500} onChange={(event) => setText(event.target.value)} />
              </label>
              <button type="button" disabled={send.busy || text.trim() === "" || windowClosed} onClick={() => void send.run()}>
                {t.send}
              </button>
            </div>
          </>
        )}
      </Async>
    </Screen>
  );
}
