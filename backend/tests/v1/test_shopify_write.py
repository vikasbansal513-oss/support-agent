import pytest

from app.integrations import shopify_write as sw
from app.v1.contracts import AddressIn

ADDRESS = AddressIn(line1="12 MG Road", city="Pune", state="Maharashtra", pin="411001")


def fake() -> sw.FakeShopify:
    shop = sw.FakeShopify()
    shop.add_order("#1001", total_paise=149_950)
    return shop


async def test_cancel_applies_and_is_idempotent_per_key() -> None:
    shop = fake()
    first = await shop.cancel_order("#1001", "changed mind", "k1")
    again = await shop.cancel_order("#1001", "changed mind", "k1")
    assert first == again and first.outcome == "ok"
    assert (await shop.read_order("#1001")).cancelled is True  # type: ignore[union-attr]


async def test_refund_amount_is_exact_and_idempotent() -> None:
    shop = fake()
    await shop.create_refund("#1001", 50_000, "r1")
    await shop.create_refund("#1001", 50_000, "r1")
    order = await shop.read_order("#1001")
    assert order is not None and order.refunded_paise == 50_000


async def test_rejected_and_upstream_error_do_not_apply() -> None:
    shop = fake()
    shop.script["order_cancel"] = ["rejected", "upstream_error"]
    assert (await shop.cancel_order("#1001", "x", "a")).outcome == "rejected"
    assert (await shop.cancel_order("#1001", "x", "b")).outcome == "upstream_error"
    assert (await shop.read_order("#1001")).cancelled is False  # type: ignore[union-attr]


async def test_timeout_applied_vs_not_applied_are_both_unknown_but_state_differs() -> None:
    shop = fake()
    shop.script["order_cancel"] = ["timeout_applied"]
    assert (await shop.cancel_order("#1001", "x", "t1")).outcome == "unknown"
    assert (await shop.read_order("#1001")).cancelled is True  # type: ignore[union-attr]
    other = fake()
    other.script["order_cancel"] = ["timeout_not_applied"]
    assert (await other.cancel_order("#1001", "x", "t2")).outcome == "unknown"
    assert (await other.read_order("#1001")).cancelled is False  # type: ignore[union-attr]


async def test_partial_refund_fails_verification() -> None:
    shop = fake()
    shop.script["refund_create"] = ["partial"]
    await shop.create_refund("#1001", 50_000, "p1")
    after = await shop.read_order("#1001")
    assert after is not None
    assert not sw.verify_effect("refund_create", 0, after, amount_paise=50_000)
    assert sw.verify_effect("refund_create", 0, after, amount_paise=25_000)


async def test_unknown_order_rejected() -> None:
    assert (await fake().cancel_order("#9", "x", "z")).outcome == "rejected"


async def test_address_and_exchange_verify() -> None:
    shop = fake()
    await shop.update_address("#1001", ADDRESS, "a1")
    await shop.create_exchange("#1001", "Kurta", "L", "e1")
    after = await shop.read_order("#1001")
    assert after is not None
    assert sw.verify_effect("address_update", 0, after, address_pin="411001")
    assert not sw.verify_effect("address_update", 0, after, address_pin="110002")
    assert sw.verify_effect("exchange_request", 0, after)


async def test_disabled_writer_is_default_and_refuses() -> None:
    sw.set_writer_override(None)
    writer = sw.get_writer()
    with pytest.raises(sw.WriteUnavailable):
        await writer.create_refund("#1001", 100, "k")


async def test_override_is_used() -> None:
    shop = fake()
    sw.set_writer_override(shop)
    try:
        assert sw.get_writer() is shop
    finally:
        sw.set_writer_override(None)
