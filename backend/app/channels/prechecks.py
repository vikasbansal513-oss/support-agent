import re
from email.utils import parseaddr

from app.contracts.contracts import NormalizedInbound

_NO_REPLY = re.compile(
    r"^(no[-_.]?reply|do[-_.]?not[-_.]?reply|donotreply|mailer-daemon|postmaster|bounce[s]?)(\+.*)?@",
    re.I,
)
_BOUNCE_SUBJECT = re.compile(
    r"(delivery status notification|undeliverable|undelivered mail|mail delivery (failed|subsystem)"
    r"|returned mail|delivery failure|failure notice)",
    re.I,
)
_OUT_OF_OFFICE = re.compile(
    r"(out of office|automatic reply|auto[- ]?reply|autoreply|away from (my )?(desk|office)"
    r"|on vacation|vacation response)",
    re.I,
)


# Boilerplate only bulk-mail senders put in the plain-text part of an HTML newsletter
# ("To view this content open the following URL in your browser: <tracking link>"). A real
# customer never writes these lines, so no sender or brand list is involved.
_BULK_BODY = re.compile(
    r"(to view this (content|email|message)[^.\n]{0,40}(open|in)[^.\n]{0,40}(url|browser|link)"
    r"|view (this|the) (email|message|newsletter) in (your )?(web )?browser"
    r"|trouble (viewing|reading) this (email|message)"
    r"|having trouble viewing)",
    re.I,
)


def email_skip_reason(inbound: NormalizedInbound, own_address: str | None) -> str | None:
    headers = {k.lower(): v for k, v in inbound.email_headers.items()}
    sender = (inbound.from_email or "").strip().lower()
    if not sender:
        return "NO_SENDER"
    if own_address and sender == own_address.strip().lower():
        return "OWN_ADDRESS"
    auto = headers.get("auto-submitted", "").strip().lower()
    if auto and auto != "no":
        return "AUTO_SUBMITTED"
    if headers.get("x-autoreply") or headers.get("x-autorespond"):
        return "AUTO_REPLY_HEADER"
    if headers.get("precedence", "").strip().lower() in ("bulk", "junk", "list", "auto_reply"):
        return "BULK_PRECEDENCE"
    if headers.get("list-unsubscribe") or headers.get("list-id"):
        return "LIST_MAIL"
    if _NO_REPLY.match(sender):
        return "NO_REPLY_SENDER"
    return_path = parseaddr(headers.get("return-path", ""))[1]
    if return_path == "" and "return-path" in headers:
        return "NULL_RETURN_PATH"
    if _BULK_BODY.search(inbound.text or ""):
        return "BULK_MAIL_BODY"
    subject = inbound.email_subject or ""
    if _BOUNCE_SUBJECT.search(subject):
        return "BOUNCE_SUBJECT"
    if _OUT_OF_OFFICE.search(subject):
        return "OUT_OF_OFFICE"
    return None
