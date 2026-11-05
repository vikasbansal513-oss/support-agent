import re
from typing import Any

from app.agent import templates as T
from app.contracts.contracts import Language

BANNED = re.compile(
    r"(refund (ho|kar|will|approved|processed)|refund ho jayega|will be refunded|cancel ho gaya"
    r"|cancelled for you|order cancel ho|guarantee|guaranteed|pakka|100%|promise|compensation"
    r"|replacement bhej|we will replace|paise wapas|money back|रिफंड हो जाएगा|गारंटी)",
    re.IGNORECASE,
)
_HASH_REF = re.compile(r"#[A-Za-z0-9-]+")
_LONG_NUMBER = re.compile(r"(?<![A-Za-z0-9])[0-9][0-9,.]{3,}(?![A-Za-z0-9])")
FACT_TEMPLATES = {"order_status", "tracking_status"}


def allowed_tokens(facts: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    order = facts.get("order")
    if order:
        tokens.add(order["order_name"])
        tokens.add(T.paise_to_rs(int(order["total_paise"])))
        for line in order["lines"]:
            tokens.add(str(line["quantity"]))
            tokens.update(t.rstrip(".,") for t in _LONG_NUMBER.findall(line["title"]))
            tokens.update(_HASH_REF.findall(line["title"]))
        tokens.update(order.get("tracking_numbers") or [])
    tracking = facts.get("tracking")
    if tracking:
        tokens.add(tracking["awb"])
    return tokens


def validate_reply(
    template_id: str, text: str, facts: dict[str, Any], disclosure_texts: set[str]
) -> str | None:
    body = text
    for disclosure in disclosure_texts:
        body = body.replace(disclosure, "")
    if BANNED.search(body):
        return "banned_phrase"
    if template_id in FACT_TEMPLATES:
        allowed = allowed_tokens(facts)
        for token in _HASH_REF.findall(body):
            if token not in allowed:
                return "unknown_order_ref"
        for token in _LONG_NUMBER.findall(_HASH_REF.sub(" ", body)):
            cleaned = token.rstrip(".,")
            if cleaned not in allowed:
                return "unknown_number"
        if not facts.get("order"):
            return "no_order_facts"
    return None


def disclosure_variants(
    policy_disclosure: dict[str, str] | None, default: dict[str, str]
) -> set[str]:
    variants = set(default.values())
    if policy_disclosure:
        variants.update(policy_disclosure.values())
    return variants


def pick_language(value: str | None, reply_language: Language = "en") -> Language:
    """Every WhatsApp/email reply goes out in `reply_language`, whatever script the
    customer used - this is the single choke point every V0/V1 reply and
    outcome-notify path routes through (see DECISIONS.md 2026-11-02).

    `value` (detected/classified customer language) is intentionally ignored: it is
    kept as a parameter only so call sites that pass the customer's language still
    type-check and read naturally. `reply_language` is the brand's own choice from
    `PolicyV2.reply_language` (Dashboard -> Policy page), default "en" - see
    Agent_Reply_TopClass_Notes.md Section 3b. Voice/calling is untouched (out of
    scope; it uses call.language directly for speech, not this function).

    Call this exactly once per reply to resolve the language, then reuse that result -
    do not pass an already-resolved language back into this function, since a second
    call with no explicit `reply_language` would silently reset it to "en".
    """
    del value
    return reply_language
