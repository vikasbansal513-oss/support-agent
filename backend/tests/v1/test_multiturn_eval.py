from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.contracts.contracts import OrderFacts, OrderLine, ToolResult
from app.core.config import get_settings
from app.integrations.shopify_write import FakeShopify, set_writer_override
from app.v1.gate import GateOrder
from tests.agent.helpers import AgentEnv
from tests.channels.helpers import wa_message
from tests.v1.flow import ADDRESS, FakeV1Tools, plan, setup


@pytest.fixture(autouse=True)
def _no_anomaly_stop(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ANOMALY_AUTO_THRESHOLD", "1000")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


LANGS = ("hi", "en", "hinglish")
YES = {
    "hi": ["हाँ", "हां कर दो", "जी", "ठीक है"],
    "en": ["yes", "yes please", "ok", "confirm"],
    "hinglish": ["haan", "haan kardo", "theek hai", "ji haan"],
}
NO = {
    "hi": ["नहीं", "रुको", "ना"],
    "en": ["no", "no no", "nope"],
    "hinglish": ["nahi", "ruko", "rehne do"],
}
UNCLEAR = {
    "hi": ["पता नहीं क्या करूँ", "बाद में बताता हूँ"],
    "en": ["not sure what to do", "let me think about it"],
    "hinglish": ["pata nahi kya karun", "hmm kal batata hoon"],
}
ASK = {
    "cancel": {
        "hi": "ऑर्डर {o} कैंसल कर दो",
        "en": "please cancel order {o}",
        "hinglish": "order {o} cancel kar do",
    },
    "address_update": {
        "hi": "ऑर्डर {o} का पता बदलना है",
        "en": "change the address of {o}",
        "hinglish": "order {o} ka address badalna hai",
    },
    "exchange_request": {
        "hi": "ऑर्डर {o} एक्सचेंज करना है",
        "en": "I want to exchange {o}",
        "hinglish": "order {o} exchange karna hai",
    },
    "refund_create": {
        "hi": "ऑर्डर {o} का रिफंड चाहिए",
        "en": "I want a refund for {o}",
        "hinglish": "order {o} ka refund chahiye",
    },
}
INTENT = {
    "cancel": "cancel",
    "address_update": "address_change",
    "exchange_request": "exchange",
    "refund_create": "refund",
}
TOOL_OF = {
    "cancel": "order_cancel",
    "address_update": "address_update",
    "exchange_request": "exchange_request",
    "refund_create": "refund_create",
}
ARGS: dict[str, dict[str, Any]] = {
    "cancel": {},
    "address_update": {"new_address": ADDRESS},
    "exchange_request": {"line_item": "Kurta", "new_variant": "L"},
    "refund_create": {"reason": "damaged"},
}
NEEDS_DELIVERED = {"exchange_request", "refund_create"}
TOTAL = 40_000


def delivered_facts(name: str, total: int) -> tuple[ToolResult, GateOrder]:
    order = OrderFacts(
        order_name=name,
        financial_status="paid",
        fulfillment_status="fulfilled",
        lines=[OrderLine(title="Kurta", quantity=1)],
        total_paise=total,
        created_at=datetime.now(UTC),
    )
    gate = GateOrder(
        order_ref=name,
        total_paise=total,
        fulfillment_status="fulfilled",
        financial_status="paid",
        cancelled=False,
        refunded_paise=0,
        delivered_at=datetime.now(UTC) - timedelta(days=2),
    )
    return ToolResult(tool="order_lookup", ok=True, order=order), gate


@dataclass
class Convo:
    n: int
    kind: str
    lang: str
    order: str
    phone: str


class Runner:
    def __init__(self, env: AgentEnv, tools: FakeV1Tools, shop: FakeShopify) -> None:
        self.env, self.tools, self.shop = env, tools, shop
        self.count = 0

    def start(self, kind: str, lang: str, total: int = TOTAL, shipped: bool | None = None) -> Convo:
        self.count += 1
        n = self.count
        order = f"#{3000 + n}"
        is_shipped = kind in NEEDS_DELIVERED if shipped is None else shipped
        if is_shipped:
            self.tools.orders[order] = delivered_facts(order, total)
            self.shop.add_order(order, total_paise=total, fulfillment_status="fulfilled")
        else:
            facts_pair = delivered_facts(order, total)
            result, gate = facts_pair
            assert result.order is not None
            result.order.fulfillment_status = "unfulfilled"
            from dataclasses import replace

            self.tools.orders[order] = (
                result,
                replace(gate, fulfillment_status="unfulfilled", delivered_at=None),
            )
            self.shop.add_order(order, total_paise=total)
        return Convo(n, kind, lang, order, f"9198100{n:05d}")

    async def say(self, convo: Convo, text: str, llm: str | None = None) -> str:
        if llm is not None:
            self.env.provider.responses = [llm]
        resp = await self.env.client.post(
            self.env.wa.path,
            json=wa_message(
                f"mt-{convo.n}-{self.count}-{len(self.env.sender.sent)}-{abs(hash(text))}",
                phone=convo.phone,
                body=text,
            ),
        )
        assert resp.status_code == 200
        await self.env.drain()
        sent = [m for m in self.env.sender.sent if m[0].lstrip("+").endswith(convo.phone[-10:])]
        return sent[-1][1] if sent else ""

    async def ask(self, convo: Convo) -> str:
        body = ASK[convo.kind][convo.lang].format(o=convo.order)
        llm = plan(
            INTENT[convo.kind],
            "propose",
            TOOL_OF[convo.kind],
            convo.order,
            args=ARGS[convo.kind],
            language=convo.lang,
        )
        return await self.say(convo, body, llm)

    async def rows(self, convo: Convo) -> list[Any]:
        return await self.env.q(
            "SELECT a.id, a.tool_name, a.status, a.amount_paise, a.order_ref FROM agent_actions a"
            " WHERE a.order_ref = :o AND a.action_type = 'tool_call'",
            o=convo.order,
        )

    def effect(self, convo: Convo) -> bool:
        written = self.shop.orders[convo.order]
        if convo.kind == "cancel":
            return written.cancelled
        if convo.kind == "address_update":
            return written.address_pin == ADDRESS["pin"]
        if convo.kind == "exchange_request":
            return written.exchange_requested
        return written.refunded_paise == TOTAL

    def calls_for(self, convo: Convo) -> int:
        return len([c for c in self.shop.calls if c["order_ref"] == convo.order])


KINDS = list(TOOL_OF)


async def test_auto_level_confirmed_conversations_write_exactly_once(env: AgentEnv) -> None:
    tools, shop = await setup(env, "auto")
    run = Runner(env, tools, shop)
    for kind in KINDS:
        for lang in LANGS:
            for yes in YES[lang]:
                convo = run.start(kind, lang)
                reply = await run.ask(convo)
                found = await run.rows(convo)
                assert len(found) == 1, (
                    kind,
                    lang,
                    yes,
                    reply,
                    await env.q("SELECT reason_code FROM handoffs"),
                )
                (pending,) = found
                assert pending.status == "awaiting_customer", (kind, lang, yes)
                assert run.calls_for(convo) == 0
                await run.say(convo, yes)
                (done,) = await run.rows(convo)
                assert done.status == "confirmed", (kind, lang, yes, done.status)
                assert done.order_ref == convo.order
                assert run.calls_for(convo) == 1 and run.effect(convo), (kind, lang, yes)
                if kind in ("refund_create", "order_cancel", "exchange_request"):
                    assert done.amount_paise == TOTAL
                await run.say(convo, yes, plan("greeting", "answer", None, ref=None))
                assert run.calls_for(convo) == 1
    assert run.count == 48


async def test_customer_declines_and_nothing_is_written(env: AgentEnv) -> None:
    tools, shop = await setup(env, "auto")
    run = Runner(env, tools, shop)
    for kind in KINDS:
        for lang in LANGS:
            for no in NO[lang]:
                convo = run.start(kind, lang)
                await run.ask(convo)
                await run.say(convo, no)
                (row,) = await run.rows(convo)
                assert row.status == "rejected", (kind, lang, no, row.status)
                assert run.calls_for(convo) == 0 and not run.effect(convo)
    assert run.count == 36


async def test_unclear_answers_hand_off_without_writing(env: AgentEnv) -> None:
    tools, shop = await setup(env, "auto")
    run = Runner(env, tools, shop)
    for kind in KINDS:
        for lang in LANGS:
            convo = run.start(kind, lang)
            await run.ask(convo)
            for text in UNCLEAR[lang]:
                await run.say(convo, text)
            (row,) = await run.rows(convo)
            assert row.status == "expired", (kind, lang, row.status)
            assert run.calls_for(convo) == 0 and not run.effect(convo)
    handoffs = await env.q("SELECT reason_code FROM handoffs")
    assert len(handoffs) == 12 and {h.reason_code for h in handoffs} == {"CONFIRM_UNCLEAR"}


async def test_approval_level_waits_for_owner_and_writes_once(env: AgentEnv) -> None:
    tools, shop = await setup(env, "approval")
    run = Runner(env, tools, shop)
    for kind in KINDS:
        for lang in LANGS:
            convo = run.start(kind, lang)
            await run.ask(convo)
            await run.say(convo, YES[lang][0])
            (row,) = await run.rows(convo)
            assert row.status == "pending_approval", (kind, lang, row.status)
            assert run.calls_for(convo) == 0
            out = await env.client.post(f"/approvals/{row.id}/approve", headers=env.wa.headers)
            assert out.json()["status"] == "confirmed", (kind, lang, out.text)
            assert run.calls_for(convo) == 1 and run.effect(convo)
            again = await env.client.post(f"/approvals/{row.id}/approve", headers=env.wa.headers)
            assert run.calls_for(convo) == 1, again.text
    assert run.count == 12


@pytest.mark.parametrize("kind", KINDS)
async def test_gate_blocks_become_handoffs_not_writes(env: AgentEnv, kind: str) -> None:
    tools, shop = await setup(env, "auto")
    run = Runner(env, tools, shop)
    cases: list[tuple[str, int, bool | None]] = [("high_value", 900_000, None)]
    if kind in ("cancel", "address_update"):
        cases.append(("already_shipped", TOTAL, True))
    else:
        cases.append(("not_delivered", TOTAL, False))
    for lang in LANGS:
        for label, total, shipped in cases:
            convo = run.start(kind, lang, total=total, shipped=shipped)
            await run.ask(convo)
            assert await run.rows(convo) == [], (kind, lang, label)
            assert run.calls_for(convo) == 0 and not run.shop.orders[convo.order].cancelled
    handoffs = await env.q("SELECT reason_code FROM handoffs")
    assert len(handoffs) == 6
    assert {h.reason_code for h in handoffs} <= {"HIGH_RISK_FLAG", "POLICY_EXCEPTION"}


async def test_answers_and_greetings_never_create_actions(env: AgentEnv) -> None:
    tools, shop = await setup(env, "auto")
    run = Runner(env, tools, shop)
    asks = {
        "hi": "मेरा ऑर्डर {o} कहाँ है",
        "en": "where is my order {o}",
        "hinglish": "mera order {o} kahan hai",
    }
    for lang in LANGS:
        for _ in range(6):
            convo = run.start("cancel", lang)
            reply = await run.say(
                convo,
                asks[lang].format(o=convo.order),
                plan("order_status", "answer", None, convo.order, language=lang),
            )
            assert convo.order in reply or reply
            assert await run.rows(convo) == []
    assert run.shop.calls == [] and run.count == 18
    set_writer_override(None)
