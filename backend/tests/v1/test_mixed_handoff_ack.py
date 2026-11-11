from datetime import UTC, datetime

from app.contracts.contracts import OrderFacts, OrderLine, ToolResult
from tests.agent.helpers import AgentEnv
from tests.v1.flow import facts, last_reply, plan, say, setup

EMAIL = (
    "Dear Customer Support Team, I am writing to inquire about the status of my order #1006, "
    "as it has been a long time since I placed the order, but it has still not been delivered. "
    "Please confirm the expected delivery date. If the order cannot be delivered soon, kindly "
    "cancel my order and initiate a refund. I would appreciate your prompt response and resolution."
)


def _order_1006() -> tuple[ToolResult, object]:
    result, gate = facts("#1006", 59_900)
    order = OrderFacts(
        order_name="#1006",
        financial_status="paid",
        fulfillment_status="unfulfilled",
        lines=[OrderLine(title="Men's Cotton Short Sleeve T-Shirt - Round Neck", quantity=1)],
        total_paise=59_900,
        created_at=datetime.now(UTC),
        customer_name="Someone Else",
    )
    return ToolResult(tool="order_lookup", ok=True, order=order), gate


async def _handoff_reply(env: AgentEnv, sentiment: float) -> str:
    tools, _ = await setup(env)
    tools.orders["#1006"] = _order_1006()  # type: ignore[assignment]
    await say(
        env,
        EMAIL,
        f"m{sentiment}",
        plan("other", "handoff", None, ref="#1006", sentiment=sentiment, language="en"),
    )
    return last_reply(env)


async def test_mixed_handoff_ack_shows_order_details_and_request(env: AgentEnv) -> None:
    reply = await _handoff_reply(env, -0.3)
    print("\n----- AGENT REPLY (unhappy customer) -----\n" + reply + "\n------------------------")
    assert "Your order *#1006*:" in reply
    assert "- Items: Men's Cotton Short Sleeve T-Shirt - Round Neck x1" in reply
    assert "- Total: *Rs 599*" in reply
    assert "not shipped yet" in reply
    assert "I have noted your request regarding cancellation and refund." in reply
    assert "I'm sorry for the trouble and the wait." in reply
    assert "30 minutes" not in reply
    assert "Someone Else" not in reply
    assert "Your reference number is" in reply


async def test_mixed_handoff_ack_has_no_apology_for_neutral_customer(env: AgentEnv) -> None:
    reply = await _handoff_reply(env, 0.0)
    print("\n----- AGENT REPLY (neutral customer) -----\n" + reply + "\n------------------------")
    assert "I'm sorry" not in reply
    assert "Your order *#1006*:" in reply
