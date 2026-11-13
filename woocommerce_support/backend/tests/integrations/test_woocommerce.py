import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.contracts.contracts import WooCommerceIn
from app.integrations.http import ExternalHttp
from app.integrations.shopify_write import WriteUnavailable
from app.integrations.woocommerce import (
    WooCommerceClient,
    basic_auth_header,
    customer_matches,
    paise,
    to_gate_order,
)
from app.integrations.woocommerce_write import RealWooCommerceWriter
from app.v1.contracts import AddressIn

Handler = Callable[[httpx.Request], httpx.Response]

STORE = "https://shop.example.com"
AUTH = basic_auth_header("ck_abcdefghij", "cs_abcdefghij")


async def _nosleep(_: float) -> None:
    return None


def make_http(handler: Handler) -> ExternalHttp:
    return ExternalHttp(
        "woocommerce", transport=httpx.MockTransport(handler), sleep=_nosleep, max_attempts=1
    )


def order(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 1001,
        "number": "1001",
        "status": "processing",
        "total": "1499.50",
        "date_created_gmt": "2026-03-01T10:00:00",
        "date_paid_gmt": "2026-03-01T10:05:00",
        "date_completed_gmt": None,
        "billing": {
            "first_name": "Asha",
            "last_name": "Rao",
            "email": "Buyer@Example.com",
            "phone": "+919876543210",
        },
        "shipping": {"address_1": "old line", "postcode": "110001", "phone": ""},
        "line_items": [{"name": "Kurta", "quantity": 2, "price": 749.75}],
        "refunds": [],
        "meta_data": [],
    }
    base.update(changes)
    return base


def client(handler: Handler) -> WooCommerceClient:
    return WooCommerceClient(make_http(handler), STORE, "ck_abcdefghij", "cs_abcdefghij")


def writer(handler: Handler) -> RealWooCommerceWriter:
    return RealWooCommerceWriter(make_http(handler), STORE, "ck_abcdefghij", "cs_abcdefghij")


def test_paise_handles_strings_and_json_numbers() -> None:
    assert paise("1499.50") == 149950
    assert paise(749.75) == 74975
    assert paise(None) == 0 and paise("") == 0 and paise("abc") == 0


def test_customer_match_uses_billing_phone_and_email() -> None:
    raw = order()
    assert customer_matches(raw, "+919876543210", None)
    assert customer_matches(raw, None, "buyer@example.com")
    assert not customer_matches(raw, "+919000000000", "other@example.com")
    assert not customer_matches(raw, None, None)


async def test_lookup_by_id_returns_facts_in_paise() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == AUTH
        assert request.url.path == "/wp-json/wc/v3/orders/1001"
        return httpx.Response(200, json=order())

    result = await client(handler).lookup_order("#1001", "+919876543210", None)
    assert result.ok and result.order is not None
    assert result.order.total_paise == 149950
    assert result.order.order_name == "#1001"
    assert result.order.financial_status == "paid"
    assert result.order.fulfillment_status == "unfulfilled"
    assert result.order.lines[0].unit_price_paise == 74975 and result.order.lines[0].quantity == 2


async def test_lookup_falls_back_to_search_when_number_is_not_the_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/orders/5001"):
            return httpx.Response(404, json={"code": "woocommerce_rest_shop_order_invalid_id"})
        assert request.url.params["search"] == "5001"
        return httpx.Response(200, json=[order(id=77, number="5001")])

    result = await client(handler).lookup_order("5001", None, "buyer@example.com")
    assert result.ok and result.order is not None and result.order.order_name == "#5001"


async def test_lookup_fails_closed_on_mismatch_missing_and_auth() -> None:
    mismatch = await client(lambda r: httpx.Response(200, json=order())).lookup_order(
        "1001", "+919000000000", "x@example.com"
    )
    assert not mismatch.ok and mismatch.error_code == "customer_mismatch"
    missing = await client(lambda r: httpx.Response(404, json=[])).lookup_order(
        "1001", "+919876543210", None
    )
    assert not missing.ok and missing.error_code == "not_found"
    denied = await client(lambda r: httpx.Response(401, json={})).lookup_order(
        "1001", "+919876543210", None
    )
    assert not denied.ok and denied.error_code == "auth_error"


async def test_probe_reports_auth_error() -> None:
    assert await client(lambda r: httpx.Response(200, json=[])).probe() is None
    failure = await client(lambda r: httpx.Response(401, json={})).probe()
    assert failure is not None and failure.error_code == "auth_error"


def test_gate_order_mapping_completed_cancelled_refunded_and_tracking() -> None:
    done = to_gate_order(order(status="completed", date_completed_gmt="2026-03-05T09:00:00"))
    assert done.fulfillment_status == "fulfilled" and done.delivered_at is not None
    assert done.tags == () and done.disputed is False
    shipped = to_gate_order(
        order(
            meta_data=[
                {"key": "_wc_shipment_tracking_items", "value": [{"tracking_number": "AWB1"}]}
            ]
        )
    )
    assert shipped.fulfillment_status == "fulfilled" and shipped.delivered_at is None
    cancelled = to_gate_order(order(status="cancelled"))
    assert cancelled.cancelled and cancelled.financial_status == "voided"
    refunded = to_gate_order(order(refunds=[{"id": 1, "total": "-1499.50"}]))
    assert refunded.financial_status == "refunded" and refunded.refunded_paise == 149950
    cod = to_gate_order(order(date_paid_gmt=None))
    assert cod.financial_status == "pending"


async def test_cancel_sends_status_and_idempotency_meta_once() -> None:
    puts: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=order())
        if request.url.path.endswith("/notes"):
            return httpx.Response(201, json={"id": 1})
        puts.append(json.loads(request.content))
        return httpx.Response(200, json=order(status="cancelled"))

    result = await writer(handler).cancel_order("#1001", "customer asked", "key-1")
    assert result.outcome == "ok"
    assert puts[0]["status"] == "cancelled"
    assert puts[0]["meta_data"][0]["key"].startswith("_sa-order_cancel-")


async def test_cancel_replay_does_not_write_again() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, json=order(status="cancelled"))

    result = await writer(handler).cancel_order("#1001", "x", "key-1")
    assert result.outcome == "ok" and result.detail == "idempotent_replay"


async def test_refund_posts_rupees_and_rejects_gateway_failure() -> None:
    posted: list[dict[str, Any]] = []

    def ok_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=order())
        if request.url.path.endswith("/refunds"):
            posted.append(json.loads(request.content))
            return httpx.Response(201, json={"id": 9})
        return httpx.Response(200, json=order())

    result = await writer(ok_handler).create_refund("#1001", 149950, "key-r")
    assert result.outcome == "ok" and result.external_ref == "woocommerce-refund-9"
    assert posted[0]["amount"] == "1499.50" and posted[0]["api_refund"] is True

    def fail_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=order())
        return httpx.Response(400, json={"code": "gateway"})

    failed = await writer(fail_handler).create_refund("#1001", 149950, "key-r2")
    assert failed.outcome == "rejected"


async def test_address_update_and_exchange_return_flags() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=order())
        if request.url.path.endswith("/notes"):
            return httpx.Response(201, json={"id": 1})
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=order())

    w = writer(handler)
    address = AddressIn(line1="12 New Street", city="Kochi", state="KL", pin="682001")
    assert (await w.update_address("#1001", address, "k1")).outcome == "ok"
    assert bodies[0]["shipping"]["postcode"] == "682001"
    assert (await w.create_exchange("#1001", "Kurta M", "Kurta L", "k2")).outcome == "ok"
    assert any(m["key"] == "_sa_exchange_requested" for m in bodies[1]["meta_data"])
    assert (await w.create_return("#1001", "wrong size", "k3")).outcome == "ok"
    assert any(m["key"] == "_sa_return_requested" for m in bodies[2]["meta_data"])


async def test_write_auth_failure_raises_write_unavailable() -> None:
    with pytest.raises(WriteUnavailable):
        await writer(lambda r: httpx.Response(401, json={})).cancel_order("#1001", "x", "k")


async def test_unreachable_store_is_unknown_not_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=order())
        raise httpx.ConnectTimeout("timeout")

    result = await writer(handler).cancel_order("#1001", "x", "k")
    assert result.outcome == "unknown"


@pytest.mark.parametrize(
    "url",
    [
        "http://shop.example.com",
        "https://localhost",
        "https://127.0.0.1",
        "https://10.0.0.5",
        "https://store.internal",
        "https://user:pw@shop.example.com",
        "https://shop.example.com?x=1",
        "https://shop.example.com:8443",
    ],
)
def test_store_url_rejects_unsafe_addresses(url: str) -> None:
    with pytest.raises(ValidationError):
        WooCommerceIn(
            store_url=url,
            consumer_key="ck_abcdefghij",
            consumer_secret="cs_abcdefghij",  # noqa: S106
        )


def test_store_url_is_normalized() -> None:
    body = WooCommerceIn(
        store_url="https://Shop.Example.com/store/",
        consumer_key="ck_abcdefghij",
        consumer_secret="cs_abcdefghij",  # noqa: S106
    )
    assert body.store_url == "https://shop.example.com/store"
