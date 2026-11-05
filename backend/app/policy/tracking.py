"""Decision Framework Section 3 - delivery/tracking dispute signals.

Pure functions, no I/O - same pattern as app.policy.fraud and app.agent.keywords
(deterministic prechecks the agent runs before it lets a tracking reply go out as a plain
status answer).

- stuck_in_transit(): Section 3's "mera order 2 din se same location par atka hua hai" /
  "parcel mere city mein aa gaya hai, phir bhi deliver nahi ho raha" pattern - "if there is
  no movement, raise a courier investigation". There is no delay-history table in this
  pipeline (TrackingFacts only ever carries the courier's LATEST scan on every lookup - a
  repeated-EDD-change pattern needs a history of past lookups this backend does not store,
  see DECISIONS.md), so that half of Section 3 is NOT buildable without inventing a new
  table/courier field. What IS answerable from the one fact already fetched on every
  tracking reply - last_update_at, the courier's own scan timestamp - is staleness: if the
  last scan is older than STUCK_THRESHOLD_HOURS and the shipment is not yet delivered, that
  alone matches "no movement" without inventing anything the courier client does not
  already return.

- is_delivered_dispute(): Section 3's named special case - "mera order delivered dikha raha
  hai, lekin mujhe mila hi nahi". This is a dispute, not a status question, and the
  framework is explicit that it must never be answered by just repeating the system's own
  "delivered" status - step 5 there says "process refund or replacement strictly according
  to the investigation result, never before it". Detected the same way app.agent.keywords
  detects ANGRY/LEGAL/etc - a fixed phrase match requiring both a "delivered" word AND a
  "but I didn't get it" phrase, so an unrelated "mera doosra order nahi mila" (about a
  different order, no delivered claim in it) does not false-positive into a forced handoff.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from app.agent.keywords import norm
from app.contracts.contracts import TrackingFacts

STUCK_THRESHOLD_HOURS = 48


def _phrases(words: list[str]) -> re.Pattern[str]:
    parts = [
        r"(?<![a-z0-9ऀ-ॿ])" + re.escape(w).replace(r"\ ", r"\s+") + r"(?![a-z0-9ऀ-ॿ])" for w in words
    ]
    return re.compile("|".join(parts))


DELIVERED_WORD = _phrases(
    [
        "delivered",
        "deliver ho gaya",
        "deliver ho gayi",
        "deliver ho chuka",
        "deliver ho chuki",
        "mil gaya dikha",
        "deliver dikha",
    ]
)
NOT_RECEIVED_PHRASE = _phrases(
    [
        "mila nahi",
        "mila hi nahi",
        "nahi mila",
        "nahi mili",
        "receive nahi hua",
        "receive nahi kiya",
        "receive nahi hui",
        "mujhe nahi mila",
        "mujhe kuch nahi mila",
        "pahuncha nahi",
        "nahi pahuncha",
        "did not receive",
        "didn't receive",
        "never received",
        "not received",
        "i did not get",
        "mujhe mila hi nahi",
    ]
)


def is_delivered_dispute(text: str) -> bool:
    """True only when the customer's own words both claim the system shows "delivered"
    AND say they personally never got it - the exact Section 3 special case, never a plain
    "order kahan hai" tracking question."""
    t = norm(text or "")
    return bool(DELIVERED_WORD.search(t) and NOT_RECEIVED_PHRASE.search(t))


def stuck_in_transit(
    tracking: TrackingFacts, now: datetime, threshold_hours: int = STUCK_THRESHOLD_HOURS
) -> bool:
    """True when the courier's own last scan is older than threshold_hours and the shipment
    is not already delivered - Section 3's "no movement" rule, using only the staleness of a
    fact the courier client already returns on every lookup (never a guessed history)."""
    if tracking.last_update_at is None:
        return False
    if "delivered" in tracking.status_text.lower():
        return False
    return (now - tracking.last_update_at) >= timedelta(hours=threshold_hours)
