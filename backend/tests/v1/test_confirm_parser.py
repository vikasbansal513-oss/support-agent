import pytest

from app.integrations.shopify import to_gate_order
from app.v1.confirm import parse_confirmation


@pytest.mark.parametrize(
    "text",
    ["haan", "Haan ji", "yes", "ok kar do", "theek hai", "हाँ", "👍", "han kar do please"],
)
def test_yes(text: str) -> None:
    assert parse_confirmation(text) == "yes"


@pytest.mark.parametrize("text", ["nahi", "no", "nahi rehne do", "नहीं"])
def test_no(text: str) -> None:
    assert parse_confirmation(text) == "no"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "haan nahi",
        "kal batata hoon",
        "kya ye safe hai?",
        "haan lekin address bhi badal do aur refund bhi",
        "pata nahi",
        "mat karo",
    ],
)
def test_unclear(text: str) -> None:
    assert parse_confirmation(text) == "unclear"


@pytest.mark.parametrize(
    "text",
    ["Yes, please cancel.", "yes please cancel", "haan cancel kar do", "yes cancel"],
)
def test_yes_with_cancel_word_resolves_with_order_cancel_context(text: str) -> None:
    # "cancel" is both a refusal word on its own ("cancel", "cancel that") and the
    # name of the action itself when the pending write is order_cancel. With that
    # tool in context, an explicit "yes" wins and "cancel" is read as describing
    # the action, not declining it (the bug: the agent used to re-ask "yes or no"
    # even though the customer had already said yes).
    assert parse_confirmation(text, tool="order_cancel") == "yes"


@pytest.mark.parametrize(
    "text",
    ["Yes, please cancel.", "yes please cancel", "haan cancel kar do", "yes cancel"],
)
def test_yes_with_cancel_word_stays_unclear_without_context(text: str) -> None:
    # Without knowing which write is pending, "yes" + "cancel" is genuinely
    # ambiguous, so it stays unclear rather than guessing.
    assert parse_confirmation(text) == "unclear"


@pytest.mark.parametrize("text", ["cancel", "cancel kar do"])
def test_lone_cancel_is_still_no_even_with_order_cancel_context(text: str) -> None:
    # A bare "cancel" (no explicit "yes") is still a refusal/stop signal, even
    # when the pending write is order_cancel - only an explicit "yes" alongside
    # it flips the reading.
    assert parse_confirmation(text, tool="order_cancel") == "no"


def test_cancel_with_another_no_word_stays_unclear_even_with_context() -> None:
    # "cancel" sits next to a second, unrelated refusal word ("mat" = don't) -
    # that combination stays genuinely ambiguous even with tool context.
    assert parse_confirmation("haan cancel mat karo", tool="order_cancel") == "unclear"


def test_gate_order_from_shopify_shape() -> None:
    gate = to_gate_order(
        {
            "name": "#1001",
            "total_price": "1499.50",
            "fulfillment_status": "fulfilled",
            "financial_status": "partially_refunded",
            "cancelled_at": None,
            "tags": "VIP, Chargeback",
            "refunds": [
                {
                    "transactions": [
                        {"kind": "refund", "amount": "100.25"},
                        {"kind": "sale", "amount": "9"},
                    ]
                }
            ],
            "fulfillments": [
                {"shipment_status": "delivered", "updated_at": "2026-03-01T10:00:00Z"}
            ],
        }
    )
    assert gate.total_paise == 149_950 and gate.refunded_paise == 10_025
    assert gate.disputed is True and gate.tags == ("chargeback", "vip")
    assert gate.delivered_at is not None and gate.cancelled is False
