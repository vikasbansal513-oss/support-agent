"""Parse Gupshup callbacks (v2 envelope per docs.gupshup.io/docs/inbound-messages). Inner payload shape for text/media
is tolerant: accepts payload.payload.text (v2) and flat payload.text. VERIFY with a captured real callback in Stage S4."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timezone
from typing import Any

E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")
MEDIA = {"image": "image", "audio": "audio", "video": "video", "file": "document", "document": "document",
         "sticker": "sticker", "location": "location", "contact": "other"}
SKIP_TYPES = {"message-event", "user-event", "billing-event", "system-event"}


def new_webhook_token() -> tuple[str, str]:
    t = secrets.token_urlsafe(32)
    return t, hash_token(t)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def tokens_equal(a_hash: str, b_hash: str) -> bool:
    return hmac.compare_digest(a_hash, b_hash)


def phone_e164(raw: str | None) -> str | None:
    d = re.sub(r"\D", "", raw or "")
    if len(d) == 10:
        d = "91" + d
    p = "+" + d
    return p if E164.match(p) else None


def parse(body: dict[str, Any]) -> dict[str, Any] | None:
    """Returns dict for NormalizedInbound, or None when the callback is not an inbound customer message (status events etc.)."""
    if not isinstance(body, dict) or body.get("type") != "message":
        return None
    p = body.get("payload")
    if not isinstance(p, dict):
        return None
    mid = p.get("id") or p.get("gsId")
    phone = phone_e164(p.get("source") or (p.get("sender") or {}).get("phone"))
    if not mid or not phone:
        return None
    inner = p.get("payload") if isinstance(p.get("payload"), dict) else {}
    ptype = str(p.get("type") or "").lower()
    text, media = "", None
    if ptype in ("text", "txt"):
        text = str(inner.get("text") or p.get("text") or "")
    elif ptype in ("button_reply", "list_reply", "quick_reply"):
        text = str(inner.get("title") or inner.get("text") or "")
    elif ptype in MEDIA:
        media = MEDIA[ptype]
        text = str(inner.get("caption") or "")
    else:
        media = "other"
    ts = body.get("timestamp")
    received = datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if isinstance(ts, (int, float)) else datetime.now(timezone.utc)
    return {"channel": "whatsapp", "external_id": str(mid), "from_phone": phone, "text": text[:20000],
            "media_type": media, "received_at": received, "_app": body.get("app")}
