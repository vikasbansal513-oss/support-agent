from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import product

from app.v1.contracts import WriteTool
from app.v1.gate import GateContext, GateOrder, evaluate
from app.v1.policy import PolicyV2
from app.voice.speech import speakable

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
TOOLS: list[WriteTool] = ["address_update", "order_cancel", "exchange_request", "refund_create"]
ALL_TOOLS = ["handoff_to_human", *TOOLS]
LANGS = ("hinglish", "en", "hi")


@dataclass(frozen=True)
class VoiceReport:
    total: int
    failed: list[str]


def _policy(level: str) -> PolicyV2:
    return PolicyV2.model_validate(
        {"enabled_tools": ALL_TOOLS, "autonomy": {t: level for t in TOOLS}, "limits": {}}
    )


def _order(total: int, delivered: bool) -> GateOrder:
    return GateOrder(
        order_ref="#1001",
        total_paise=total,
        fulfillment_status="fulfilled" if delivered else "unfulfilled",
        financial_status="paid",
        cancelled=False,
        refunded_paise=0,
        delivered_at=NOW - timedelta(days=2) if delivered else None,
    )


def run_gate_cases() -> VoiceReport:
    failed: list[str] = []
    total = 0
    for tool, level, amount in product(
        TOOLS, ("off", "approval", "auto"), (9_900, 40_000, 150_000)
    ):
        delivered = tool in ("refund_create", "exchange_request")
        order = _order(amount, delivered)
        text_d = evaluate(_policy(level), GateContext(tool, order, NOW))
        voice_d = evaluate(_policy(level), GateContext(tool, order, NOW, channel="voice"))
        total += 1
        name = f"gate:{tool}:{level}:{amount}"
        if voice_d.amount_paise != text_d.amount_paise:
            failed.append(f"{name}:amount_changed")
        if voice_d.route == "auto" and tool in ("refund_create", "exchange_request"):
            failed.append(f"{name}:voice_auto_money")
        if text_d.route != "auto" and voice_d.route != text_d.route:
            failed.append(f"{name}:voice_loosened")
        if (
            text_d.route == "auto"
            and tool in ("order_cancel", "address_update")
            and (voice_d.route != "auto")
        ):
            failed.append(f"{name}:voice_blocked_safe_tool")
        if (
            text_d.route == "auto"
            and tool in ("refund_create", "exchange_request")
            and (voice_d.route != "approval")
        ):
            failed.append(f"{name}:voice_not_routed_to_approval")
    return VoiceReport(total, failed)


def run_speech_cases() -> VoiceReport:
    failed: list[str] = []
    total = 0
    amounts = [("Rs 400", "400"), ("Rs 1,250", "1250"), ("Rs 99.50", "99"), ("Rs. 5000", "5000")]
    for (raw, spoken), lang in product(amounts, LANGS):
        total += 1
        out = speakable(f"Order #1001 ka total {raw} hai.", lang)
        name = f"speech:{raw}:{lang}"
        if spoken not in out or "Rs" in out or "#" in out or "1 0 0 1" not in out:
            failed.append(name)
    for junk in ("*bold*", "`code`", "<b>tag</b>", "[link](x)", "{x}", "a|b", "😀🙏"):
        total += 1
        out = speakable(f"Theek hai {junk} ji", "hinglish")
        if any(ch in out for ch in "*`<>[]{}|\\") or any(ord(ch) > 0x1F000 for ch in out):
            failed.append(f"speech:markup:{junk}")
    for lang in LANGS:
        total += 1
        if len(speakable("bohot lamba jawab " * 200, lang)) > 400:
            failed.append(f"speech:cap:{lang}")
    swaps = [("hinglish", "'haan' likhein", "bolein"), ("en", "Reply 'yes'", "say")]
    for lang, raw, wanted in swaps:
        total += 1
        out = speakable(raw, lang)
        if wanted not in out.lower() or "likhein" in out or "Reply" in out:
            failed.append(f"speech:swap:{lang}")
    return VoiceReport(total, failed)


def run_all() -> VoiceReport:
    parts = [run_gate_cases(), run_speech_cases()]
    return VoiceReport(sum(p.total for p in parts), [f for p in parts for f in p.failed])
