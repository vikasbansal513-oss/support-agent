import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.agent import templates as v0
from app.agent.validate import BANNED
from app.core.masking import redact_for_llm
from app.llm.client import LlmClient, LlmInvalid, LlmUnavailable, load_prompt, split_prompt
from app.llm.guard import wrap_untrusted
from app.v1.contracts import Promise, ReplyDraft
from app.v1.policy import PolicyV2

REPLY_PROMPT = "reply.v1.md"
REPLY_VERSION = "reply.v1"
MAX_REPLY_CHARS = 900
EXTRA_BANNED = re.compile(
    r"(discount|coupon|free of charge|kal tak|by tomorrow|within \d+ (hours|days)|exception|"
    r"waive|compensat|sorry for the delay in refund)",
    re.IGNORECASE,
)
INTERNAL = re.compile(
    r"([A-Z]{3,}_[A-Z_]{2,}|(?i:\bprompt\b|\bsystem message\b|\btool_results?\b|"
    r"\bpolicy engine\b|\bfacts? (were|was) given\b|\bjson\b))"
)
_NUMBER = re.compile(r"(?<![A-Za-z0-9])\d[\d,]*(?:\.\d+)?(?![A-Za-z0-9])")
_HASH_REF = re.compile(r"#[A-Za-z0-9-]+")
_AWB = re.compile(r"\b[A-Za-z]*\d{8,}[A-Za-z0-9]*\b")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


@dataclass
class ReplyOutcome:
    draft: ReplyDraft | None
    attempts: int
    problem: str | None
    needs_human: bool = False


def order_facts(order: dict[str, Any], lang: str) -> dict[str, Any]:
    items = ", ".join(f"{line['title']} x{line['quantity']}" for line in order["lines"])[:200]
    return {
        "order_name": order["order_name"],
        "status": v0.status_word(v0.FULFILL, order["fulfillment_status"], lang),
        "payment": v0.status_word(v0.PAY, order["financial_status"], lang),
        "items": items or "-",
        "total": f"Rs {v0.paise_to_rs(int(order['total_paise']))}",
    }


def tracking_facts(tracking: dict[str, Any]) -> dict[str, Any]:
    return {
        "courier": tracking["courier"],
        "awb": tracking["awb"],
        "status": tracking["status_text"],
    }


def policy_facts(policy: PolicyV2) -> dict[str, Any]:
    hours = policy.office_hours
    return {
        "return_window_days": policy.return_window_days,
        "final_sale_tags": list(policy.final_sale_tags),
        "support_hours": f"{hours.start} to {hours.end}",
        "team_reply_within_minutes": policy.handoff_sla_minutes,
    }


def _digits(value: object) -> set[str]:
    return {m.replace(",", "") for m in _NUMBER.findall(json.dumps(value, ensure_ascii=False))}


def validate_reply_v1(
    draft: ReplyDraft,
    facts: dict[str, Any],
    customer_text: str,
    expected_language: str,
    allowed_promises: frozenset[Promise] = frozenset(),
) -> str | None:
    body = draft.text
    if len(body) > MAX_REPLY_CHARS:
        return "too_long"
    if BANNED.search(body) or EXTRA_BANNED.search(body):
        return "banned_phrase"
    if INTERNAL.search(body):
        return "internal_words"
    if set(draft.promises) - allowed_promises:
        return "unconfirmed_promise"
    has_devanagari = bool(_DEVANAGARI.search(body))
    if expected_language == "hi" and not has_devanagari:
        return "wrong_language"
    if expected_language in ("en", "hinglish") and has_devanagari:
        return "wrong_language"
    allowed_numbers = _digits(facts) | {m.replace(",", "") for m in _NUMBER.findall(customer_text)}
    allowed_refs = {r.lower() for r in _HASH_REF.findall(json.dumps(facts, ensure_ascii=False))} | {
        r.lower() for r in _HASH_REF.findall(customer_text)
    }
    for ref in _HASH_REF.findall(body):
        if ref.lower() not in allowed_refs:
            return "unknown_order_ref"
    for token in _NUMBER.findall(_HASH_REF.sub(" ", body)):
        if token.replace(",", "").rstrip(".") not in allowed_numbers:
            return "unknown_number"
    for token in _AWB.findall(body):
        if token not in json.dumps(facts, ensure_ascii=False):
            return "unknown_tracking_id"
    return None


async def compose(
    llm: LlmClient,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID | None,
    facts: dict[str, Any],
    customer_text: str,
    history: str,
    language: str,
    tone_notes: str,
) -> ReplyOutcome:
    system = split_prompt(load_prompt(REPLY_PROMPT)).replace(
        "{{tone}}", redact_for_llm(tone_notes, 500) or "neutral and polite"
    )
    safe_text = redact_for_llm(customer_text)
    base = (
        f"<facts>{json.dumps(facts, ensure_ascii=False)}</facts>\n"
        f"<language>{language}</language>\n"
        f"{wrap_untrusted('history', history or '(none)', 3000)}\n"
        f"{wrap_untrusted('customer_message', safe_text, 2000)}"
    )
    problem: str | None = None
    for attempt in (1, 2):
        user = (
            base
            if problem is None
            else f"{base}\n\nYour previous reply was rejected: {problem}. Write it again using only the facts."
        )
        try:
            draft = await llm.complete_json(
                tenant_id=tenant_id,
                run_id=run_id,
                purpose="reply",
                system=system,
                user=user,
                schema=ReplyDraft,
                prompt_version=REPLY_VERSION,
            )
        except (LlmUnavailable, LlmInvalid, ValidationError):
            return ReplyOutcome(None, attempt, "llm_failed")
        if draft.needs_human:
            return ReplyOutcome(None, attempt, "needs_human", needs_human=True)
        problem = validate_reply_v1(draft, facts, customer_text, language)
        if problem is None:
            return ReplyOutcome(draft, attempt, None)
    return ReplyOutcome(None, 2, problem)
