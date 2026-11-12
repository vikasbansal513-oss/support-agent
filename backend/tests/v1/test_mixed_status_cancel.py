from app.v1 import templates as t
from app.v1.graph import _request_note
from tests.agent.helpers import AgentEnv
from tests.v1.flow import actions, last_reply, plan, say, setup

MIXED = (
    "Please confirm the expected delivery date of order #1001. If it cannot be delivered soon, "
    "kindly cancel my order and initiate the refund."
)


async def test_status_plus_cancel_answers_status_and_asks_yes_no(env: AgentEnv) -> None:
    _, shop = await setup(env, "auto")
    await say(
        env,
        MIXED,
        "s1",
        plan(language="en", secondary_answers=["order_status"]),
    )
    reply = last_reply(env)
    assert "has not shipped yet" in reply
    assert "no expected delivery date" in reply
    assert "YES" in reply and "NO" in reply
    (pending,) = await actions(env, "order_cancel")
    assert pending.status == "awaiting_customer" and not pending.requires_approval
    assert shop.calls == []
    await say(env, "yes", "s2")
    assert "has been cancelled" in last_reply(env)
    assert shop.orders["#1001"].cancelled is True and len(shop.calls) == 1


def test_not_received_is_not_a_complaint() -> None:
    note = _request_note("I have still not received my order, please cancel it", "en")
    assert "complaint" not in note and "cancellation" in note
    assert "complaint" in _request_note("the item arrived damaged", "en")


def test_greeting_uses_only_the_senders_first_name() -> None:
    assert t.greeting("en", "Vikas Bansal") == "Dear Vikas,"
    assert t.greeting("en", "manisha bansal") == "Dear Manisha,"
    assert t.greeting("hi", "विकास बंसल") == "प्रिय विकास,"
    assert t.greeting("en", None) == "Dear Customer,"
    assert t.greeting("en", "   ") == "Dear Customer,"
    assert t.greeting("en", "vikas@gmail.com") == "Dear Customer,"
    assert t.greeting("en", "12345") == "Dear Customer,"
    assert t.greeting("en", "Vik<b>as") == "Dear Vikbas,"
