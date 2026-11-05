import base64
import re
from datetime import UTC, datetime
from email.utils import parseaddr
from typing import Any

from pydantic import ValidationError

from app.channels import gupshup_parse
from app.contracts.contracts import NormalizedInbound

KEPT_HEADERS = (
    "auto-submitted",
    "x-autoreply",
    "x-autorespond",
    "precedence",
    "list-unsubscribe",
    "list-id",
    "from",
    "return-path",
    "message-id",
    "references",
    "authentication-results",
    "reply-to",
    "x-auto-response-suppress",
)
_QUOTE_MARKER = re.compile(
    r"^(on .{5,120} wrote:|-{2,}\s*original message\s*-{2,}|from:\s.+)$", re.I
)
_TAGS = re.compile(r"<[^>]+>")


def from_gupshup(body: dict[str, Any]) -> NormalizedInbound | None:
    parsed = gupshup_parse.parse(body)
    if parsed is None:
        return None
    parsed.pop("_app", None)
    try:
        return NormalizedInbound(**parsed)
    except ValidationError:
        return None


def _decode(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode()).decode("utf-8", errors="replace")


def _walk(part: dict[str, Any], mime: str) -> str | None:
    if part.get("mimeType") == mime and (part.get("body") or {}).get("data"):
        return _decode(part["body"]["data"])
    for child in part.get("parts") or []:
        found = _walk(child, mime)
        if found:
            return found
    return None


def strip_quoted(text: str) -> str:
    kept: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if _QUOTE_MARKER.match(stripped) and kept:
            break
        if stripped.startswith(">"):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _attachment_names(part: dict[str, Any], out: list[str]) -> None:
    filename = str(part.get("filename") or "").strip()
    if filename and (part.get("body") or {}).get("attachmentId"):
        out.append(filename[:200])
    for child in part.get("parts") or []:
        _attachment_names(child, out)


def email_attachment_names(payload: dict[str, Any]) -> list[str]:
    out: list[str] = []
    _attachment_names(payload, out)
    return out[:20]


def email_body(payload: dict[str, Any]) -> str:
    plain = _walk(payload, "text/plain")
    if plain:
        return strip_quoted(plain)
    html = _walk(payload, "text/html")
    if html:
        return strip_quoted(re.sub(r"\s+", " ", _TAGS.sub(" ", html)).strip())
    return ""


def from_gmail(message: dict[str, Any]) -> NormalizedInbound | None:
    payload = message.get("payload") or {}
    headers = {
        str(h.get("name", "")).lower(): str(h.get("value", ""))
        for h in payload.get("headers") or []
        if isinstance(h, dict)
    }
    sender = parseaddr(headers.get("from", ""))[1].strip().lower()
    message_id = message.get("id")
    if not sender or "@" not in sender or not message_id:
        return None
    millis = message.get("internalDate")
    received = (
        datetime.fromtimestamp(int(millis) / 1000, tz=UTC)
        if isinstance(millis, str | int) and str(millis).isdigit()
        else datetime.now(UTC)
    )
    try:
        return NormalizedInbound(
            channel="email",
            external_id=str(message_id),
            from_email=sender,
            text=email_body(payload)[:20000],
            email_headers={k: v[:500] for k, v in headers.items() if k in KEPT_HEADERS},
            email_subject=headers.get("subject", "")[:300] or None,
            email_thread_id=str(message["threadId"]) if message.get("threadId") else None,
            email_attachment_names=email_attachment_names(payload),
            received_at=received,
        )
    except ValidationError:
        return None
