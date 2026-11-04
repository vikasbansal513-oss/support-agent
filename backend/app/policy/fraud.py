"""Claim-level fraud/abuse signals from the Decision Framework, Section 12.

Pure functions, no I/O - mirrors app.agent.keywords (deterministic prechecks before any
write is proposed or approved). Two independent things live here:

- requires_evidence(): claim-reason categories (wrong item, empty box, item not as
  described, damaged, missing item/accessory, used/old condition, near-expiry) where
  Section 12.3 makes photo/video evidence mandatory before any refund, replacement or
  exchange is promised. The agent has no automated way to check a photo (no vision model
  in this pipeline, no courier-weight/SKU cross-check integration - see DECISIONS.md), so
  "evidence mandatory" is enforced by never auto-proposing these claims: the agent asks for
  a photo, and the moment one arrives the existing MEDIA_RECEIVED precheck (app.v1.graph)
  hands the case to a human with it attached. A claim with no reason text, or a reason that
  does not match an evidence-requiring category (e.g. "no longer needed", duplicate order,
  size issue with no condition dispute), is unaffected and follows the normal gate.

- Claim-clustering (Section 12.4/12.5: "several similar claims clustering in a short time
  window... individually ordinary; together the signature of organised activity") is NOT a
  text-classifier concern - it is a count of the same customer's recent refund/exchange/
  return claims, which only the database can answer. That check lives in app.v1.gate
  (GateContext.recent_claim_count), fed by app.v1.regate.customer_context(), so it runs
  through the exact same evaluate() both on first proposal and on the pre-execution regate.

Bracketing (Section 12.2 - ordering several sizes/colours, keeping one) is explicitly NOT
flagged here: the framework states this is normal shopping behaviour, not fraud, and must
never add friction or a restocking fee the brand's own policy doesn't already define. No
function in this module treats a plain size/colour exchange or multi-item return as a signal.

- Wardrobing (Section 12.1 - wearing/using an item once then returning it as unused) is only
  partially buildable here. The framework's own check is visual ("are tags/seal intact",
  "does the photo show wear") - there is no vision model in this pipeline to answer that, so
  that half is NOT implemented (same limitation as 12.3's courier-weight/SKU cross-check -
  see DECISIONS.md). What IS buildable with text alone: when the customer's own words admit
  prior use ("ek baar pehna tha", "used it once", "tag hata diya tha"), that is itself a
  strong signal requiring the same evidence-first handling as 12.3 - requires_evidence()
  below folds this into the same check and the same ask-for-a-photo flow, since the
  enforcement mechanism (never auto-approve, ask for a photo, let a human judge it when one
  arrives) is identical either way.
"""

from __future__ import annotations

import re

from app.agent.keywords import norm


# Same word/phrase-boundary technique as app.agent.keywords._rx, kept local rather than
# importing that private helper across packages.
def _phrases(words: list[str]) -> re.Pattern[str]:
    parts = [
        r"(?<![a-z0-9ऀ-ॿ])" + re.escape(w).replace(r"\ ", r"\s+") + r"(?![a-z0-9ऀ-ॿ])" for w in words
    ]
    return re.compile("|".join(parts))


EVIDENCE_REQUIRED = _phrases(
    [
        # wrong / switched item
        "wrong item",
        "wrong product",
        "galat item",
        "galat product",
        "dusra product",
        "doosra product",
        "alag product",
        "kuch aur bhej",
        # empty box / item not as described
        "empty box",
        "khali box",
        "box khali",
        "andar kuch nahi",
        "andar kuch tha hi nahi",
        "kuch mila hi nahi",
        "box mein kuch nahi",
        "not as described",
        "photo se alag",
        "image se alag",
        "fake",
        "nakli",
        "duplicate product",
        "quality bahut kharab",
        # damaged / broken
        "damaged",
        "damage",
        "broken",
        "toota",
        "tuta",
        "tooti",
        "crack",
        "leak",
        "leaking",
        # missing item / accessory
        "missing item",
        "missing accessory",
        "item missing",
        "charger missing",
        "accessory missing",
        "item kam",
        "kam aaya",
        # used / old condition
        "already used",
        "pehle se used",
        "used product mila",
        "purana laptop",
        "purana product",
        "second hand",
        # expiry / safety-adjacent condition claims
        "expiry nikal",
        "expired product",
        "expiry bahut paas",
    ]
)

# Section 12.1 - wardrobing self-admission. A plain "doesn't fit" / "size chhota hai" /
# "no longer needed" return (bracketing, Section 12.2) is deliberately NOT here - only an
# explicit admission of prior use is treated as a signal.
WARDROBING_ADMISSION = _phrases(
    [
        "used it once",
        "used once",
        "wore it once",
        "wore once",
        "worn once",
        "ek baar pehna",
        "ek baar pehan",
        "ek baar use kiya",
        "ek baar istemal",
        "function me pehna",
        "function ke liye pehna",
        "shaadi me pehna",
        "party me pehna",
        "event ke liye pehna",
        "pehn liya tha ek baar",
        "tag hata diya",
        "tag nikal diya",
        "seal khol diya tha",
        "already worn",
    ]
)


def requires_evidence(reason: str | None) -> bool:
    """True if this claim reason is one Section 12.3 (or 12.1's text-detectable half)
    requires photo/video evidence for before any refund, replacement or exchange is
    promised. A missing or unmatched reason returns False - the normal gate/eligibility
    checks still apply either way."""
    if not reason:
        return False
    text = norm(reason)
    return bool(EVIDENCE_REQUIRED.search(text) or WARDROBING_ADMISSION.search(text))
