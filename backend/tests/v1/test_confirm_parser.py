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
