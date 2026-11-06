"""Incoming-email classification (spec: Email_Filter_v2.2.md).

Runs once per genuine inbound email, after channels.prechecks has already
short-circuited bounces/auto-replies/no-reply senders for free, and before
identity.get_or_create_customer / any workflow or reply. See
jobs/handlers.py::_ingest() for the call site.

Three fields are always computed in code and the LLM only echoes them back
(Section 3, 4, 5 of the spec): sender_authentication, has_attachment /
security_status, and is_duplicate (always False here - a true duplicate
message-id never reaches this function at all, see Section 5).

A fourth thing is forced in code, not computed: `action` is pushed to SKIP
whenever the LLM's own is_customer_related/is_marketing/is_automated/is_spam
fields agree the mail is not about this brand's customers, even if the LLM
picked action=MANUAL_REVIEW out of caution. See `_should_force_skip()`.
"""

from __future__ import annotations

import re
import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.contracts.contracts import (
    EmailClassification,
    NormalizedInbound,
    SenderAuthentication,
)
from app.core.masking import redact_for_llm
from app.integrations.http import DbCallRecorder as HttpCallRecorder
from app.integrations.http import ExternalHttp
from app.llm.client import DbCallRecorder as LlmCallRecorder
from app.llm.client import LlmAuthError, LlmClient, LlmInvalid, LlmUnavailable, load_prompt
from app.llm.guard import wrap_untrusted
from app.llm.provider import make_provider
from app.llm.runtime import load_effective
from app.v1.policy import PolicyV2

Factory = async_sessionmaker[AsyncSession]


async def build_llm_client(
    factory: Factory, tenant_id: uuid.UUID, transport: httpx.AsyncBaseTransport | None
) -> LlmClient:
    """Same construction as app.agent.runner._llm - duplicated (not imported) because
    agent.runner imports app.jobs.handlers, and this module is called from there."""
    chosen = await load_effective(factory)
    http = ExternalHttp(
        chosen.provider,
        recorder=HttpCallRecorder(factory, tenant_id),
        transport=transport,
    )
    provider = make_provider(http, chosen.provider, chosen.model, chosen.api_key, chosen.base_url)
    return LlmClient(provider, LlmCallRecorder(factory))


PROMPT_NAME = "email_filter.v1.md"
PROMPT_VERSION = "email_filter.v1"

_AUTH_TOKEN = re.compile(r"(spf|dkim|dmarc)=(\w+)", re.IGNORECASE)
_VALID_AUTH = {"pass", "fail", "softfail", "neutral", "none"}

_HIGH_RISK_EXT = frozenset({".exe", ".scr", ".bat", ".cmd", ".js", ".vbs", ".jar", ".msi", ".ps1"})
_NEEDS_REVIEW_EXT = frozenset({".docm", ".xlsm", ".pptm", ".zip", ".rar"})


def _should_force_skip(result: EmailClassification) -> bool:
    """Category-name-agnostic safety net (user requirement: "koi bhi website ka naam
    ho, use skip kare" - never a per-sender/per-brand list). The LLM already returns
    is_customer_related/is_marketing/is_automated/is_spam for every email (Section 6
    of the spec); this was being computed but never actually used to drive `action`,
    so an ad/newsletter/job-alert/stock-broker-statement/SaaS-tool/OTP mail that the
    LLM wasn't 100% sure how to *categorize* (e.g. category=UNCLEAR) still fell back
    to MANUAL_REVIEW and reached a human, even when it was clearly not about this
    brand's customers.

    Force SKIP only when the LLM itself says the mail is not customer-related AND at
    least one of marketing/automated/spam is true - i.e. two independent signals from
    the LLM's own reading of the email agree it is noise. A genuine customer message
    (is_customer_related=true) is never touched here, whatever else is true about it.
    """
    if result.is_customer_related:
        return False
    if result.category in ("PHISHING", "SUSPICIOUS", "SECURITY_ALERT", "OTHER"):
        return False
    return bool(result.is_marketing or result.is_automated or result.is_spam)


def parse_sender_authentication(headers: dict[str, str]) -> SenderAuthentication:
    """Section 3: the ONLY source of sender_authentication. Never let the LLM invent it."""
    raw = headers.get("authentication-results", "")
    found = {m.group(1).lower(): m.group(2).lower() for m in _AUTH_TOKEN.finditer(raw)}
    return SenderAuthentication(
        spf=found.get("spf", "unknown") if found.get("spf") in _VALID_AUTH else "unknown",
        dkim=found.get("dkim", "unknown") if found.get("dkim") in _VALID_AUTH else "unknown",
        dmarc=found.get("dmarc", "unknown") if found.get("dmarc") in _VALID_AUTH else "unknown",
    )


def _extension(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot != -1 else ""


def attachment_security_status(attachment_names: list[str]) -> str:
    """Section 4: deterministic heuristic, no scanner connected. Never let the LLM
    claim a scan result it never performed."""
    extensions = {_extension(name) for name in attachment_names}
    if extensions & _HIGH_RISK_EXT:
        return "high_risk_extension"
    if extensions & _NEEDS_REVIEW_EXT:
        return "needs_review"
    return "not_checked"


def _user_prompt(
    inbound: NormalizedInbound, auth: SenderAuthentication, security_status: str
) -> str:
    headers = inbound.email_headers
    safe_subject = redact_for_llm(inbound.email_subject or "", 300)
    safe_body = redact_for_llm(inbound.text, 4000)
    computed = (
        "sender_authentication: "
        f"spf={auth.spf} dkim={auth.dkim} dmarc={auth.dmarc}\n"
        f"has_attachment: {bool(inbound.email_attachment_names)}\n"
        f"security_status: {security_status}\n"
        "is_duplicate: false"
    )
    email_block = (
        f"FROM: {redact_for_llm(inbound.from_email or '', 200)}\n"
        f"REPLY-TO: {redact_for_llm(headers.get('reply-to', ''), 200)}\n"
        f"SUBJECT: {safe_subject}\n\n"
        f"{safe_body}"
    )
    return f"<code_computed>\n{computed}\n</code_computed>\n" + wrap_untrusted(
        "untrusted_data", email_block, 4600
    )


async def classify_email(
    llm: LlmClient,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID | None,
    policy: PolicyV2 | None,
    inbound: NormalizedInbound,
) -> EmailClassification:
    auth = parse_sender_authentication(inbound.email_headers)
    security_status = attachment_security_status(inbound.email_attachment_names)
    has_attachment = bool(inbound.email_attachment_names)

    system = load_prompt(PROMPT_NAME)
    user = _user_prompt(inbound, auth, security_status)
    try:
        result = await llm.complete_json(
            tenant_id=tenant_id,
            run_id=run_id,
            purpose="email_classify",
            system=system,
            user=user,
            schema=EmailClassification,
            prompt_version=PROMPT_VERSION,
        )
    except (LlmAuthError, LlmUnavailable, LlmInvalid):
        result = EmailClassification(
            category="UNCLEAR",
            action="MANUAL_REVIEW",
            reason="llm_unavailable_or_invalid",
        )

    # Section 3/4/5: these four fields are code-computed facts. The prompt tells the
    # LLM to echo them back unchanged, but a misbehaving/invalid completion must
    # never override them - re-assert here regardless of what the LLM returned.
    forced_action = result.action
    if security_status == "high_risk_extension":
        forced_action = "QUARANTINE"
    elif forced_action not in ("SKIP", "SKIP_DUPLICATE", "QUARANTINE") and _should_force_skip(
        result
    ):
        forced_action = "SKIP"
    result = result.model_copy(
        update={
            "sender_authentication": auth,
            "has_attachment": has_attachment,
            "security_status": security_status,
            "is_duplicate": False,
            "action": forced_action,
        }
    )

    if policy is not None and security_status != "high_risk_extension" and not result.is_duplicate:
        override = policy.email_filter_overrides.get(result.category)
        if override is not None and override != result.action:
            result = result.model_copy(update={"action": override})
    return result
