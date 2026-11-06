import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.http import CallRecord, ExternalHttp
from app.integrations.shopify_write import (
    RealShopifyWriter,
    WriteUnavailable,
    get_writer_for_tenant,
    set_writer_override,
)
from app.v1.contracts import AddressIn
from tests.auth.helpers import MemoryMailer, active_user, bearer, login, set_tenant_status
from tests.integrations.test_api import SHOP, Upstream

Factory = async_sessionmaker[AsyncSession]
Handler = Callable[[httpx.Request], httpx.Response]
ADDRESS = AddressIn(line1="12 MG Road", city="Pune", state="Maharashtra", pin="411001")


async def _nosleep(_: float) -> None:
    return None


def make_http(handler: Handler) -> ExternalHttp:
    async def recorder(record: CallRecord) -> None:
        return None

    return ExternalHttp(
        "shopify", recorder=recorder, transport=httpx.MockTransport(handler), sleep=_nosleep
    )


class FakeShopifyRest:
    """In-memory Shopify Admin REST server: just enough of orders.json / cancel.json /
    refunds.json / transactions.json / orders/{id}.json (PUT) for RealShopifyWriter."""

    def __init__(self, order: dict[str, Any]) -> None:
        self.order = dict(order)
        self.cancel_calls = 0
        self.refund_calls = 0
        self.tag_calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["x-shopify-access-token"] == "tok-abcd1234"
        path = request.url.path
        if path.endswith("/orders.json"):
            name = request.url.params["name"]
            match = [self.order] if self.order.get("name") == name else []
            return httpx.Response(200, json={"orders": match})
        if path.endswith("/cancel.json"):
            self.cancel_calls += 1
            self.order["cancelled_at"] = "2026-03-01T10:00:00+05:30"
            return httpx.Response(200, json={"order": self.order})
        if path.endswith("/transactions.json"):
            return httpx.Response(
                200,
                json={
                    "transactions": [
                        {"id": 9001, "kind": "sale", "status": "success", "gateway": "bogus"}
                    ]
                },
            )
        if path.endswith("/refunds.json"):
            self.refund_calls += 1
            return httpx.Response(200, json={"refund": {"id": 7001}})
        if path.rstrip("/").endswith(f"orders/{self.order['id']}.json") and request.method == "PUT":
            self.tag_calls += 1
            import json as _json

            body = _json.loads(request.content)["order"]
            self.order.update(body)
            return httpx.Response(200, json={"order": self.order})
        raise AssertionError(f"unexpected call: {request.method} {path}")


def order(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 555,
        "name": "#1001",
        "tags": "",
        "total_price": "1499.50",
        "fulfillment_status": "unfulfilled",
        "cancelled_at": None,
        "refunds": [],
        "shipping_address": {"address1": "old line", "zip": "110001"},
        "phone": "+919876543210",
        "email": None,
        "note": None,
    }
    base.update(overrides)
    return base


async def test_cancel_order_applies_once_and_is_idempotent_on_retry() -> None:
    server = FakeShopifyRest(order())
    writer = RealShopifyWriter(make_http(server.handler), "a.myshopify.com", "tok-abcd1234")
    first = await writer.cancel_order("#1001", "changed mind", "k1")
    assert first.outcome == "ok"
    after = await writer.read_order("#1001")
    assert after is not None and after.cancelled is True
    assert server.cancel_calls == 1
    again = await writer.cancel_order("#1001", "changed mind", "k1")
    assert again.outcome == "ok" and again.detail == "idempotent_replay"
    assert server.cancel_calls == 1  # not called a second time


async def test_refund_uses_original_sale_transaction() -> None:
    server = FakeShopifyRest(order())
    writer = RealShopifyWriter(make_http(server.handler), "a.myshopify.com", "tok-abcd1234")
    result = await writer.create_refund("#1001", 50_000, "r1")
    assert result.outcome == "ok" and result.external_ref == "shopify-refund-7001"
    assert server.refund_calls == 1
    await writer.create_refund("#1001", 50_000, "r1")
    assert server.refund_calls == 1  # idempotent replay, no second refund call


async def test_refund_without_capturable_transaction_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/orders.json"):
            return httpx.Response(200, json={"orders": [order()]})
        if request.url.path.endswith("/transactions.json"):
            return httpx.Response(200, json={"transactions": []})
        raise AssertionError("unexpected call")

    writer = RealShopifyWriter(make_http(handler), "a.myshopify.com", "tok-abcd1234")
    result = await writer.create_refund("#1001", 50_000, "r1")
    assert result.outcome == "rejected" and result.detail == "no_capturable_transaction"


async def test_update_address_success() -> None:
    server = FakeShopifyRest(order())
    writer = RealShopifyWriter(make_http(server.handler), "a.myshopify.com", "tok-abcd1234")
    result = await writer.update_address("#1001", ADDRESS, "a1")
    assert result.outcome == "ok"
    after = await writer.read_order("#1001")
    assert after is not None and after.address_pin == "411001"


async def test_exchange_and_return_tag_and_note_the_order() -> None:
    server = FakeShopifyRest(order())
    writer = RealShopifyWriter(make_http(server.handler), "a.myshopify.com", "tok-abcd1234")
    exchange = await writer.create_exchange("#1001", "Kurta", "L", "e1")
    assert exchange.outcome == "ok"
    after = await writer.read_order("#1001")
    assert after is not None and after.exchange_requested is True
    assert "Kurta" in str(server.order["note"]) and "L" in str(server.order["note"])
    ret = await writer.create_return("#1001", "doesn't fit", "rt1")
    assert ret.outcome == "ok"
    after2 = await writer.read_order("#1001")
    assert after2 is not None and after2.return_requested is True


async def test_order_not_found_is_rejected_not_an_exception() -> None:
    writer = RealShopifyWriter(
        make_http(lambda r: httpx.Response(200, json={"orders": []})),
        "a.myshopify.com",
        "tok-abcd1234",
    )
    result = await writer.cancel_order("#9999", "x", "k")
    assert result.outcome == "rejected" and result.detail == "order_not_found"
    assert await writer.read_order("#9999") is None


async def test_auth_failure_raises_write_unavailable() -> None:
    writer = RealShopifyWriter(
        make_http(lambda r: httpx.Response(401)), "a.myshopify.com", "tok-bad"
    )
    with pytest.raises(WriteUnavailable):
        await writer.cancel_order("#1001", "x", "k")
    with pytest.raises(WriteUnavailable):
        await writer.read_order("#1001")


async def test_exhausted_retries_return_unknown_not_rejected() -> None:
    def always_503(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    writer = RealShopifyWriter(make_http(always_503), "a.myshopify.com", "tok-abcd1234")
    result = await writer.cancel_order("#1001", "x", "k")
    assert result.outcome == "unknown" and result.detail is not None
    assert result.detail.startswith("upstream:")


async def test_read_order_maps_refunds_and_customer_fields() -> None:
    raw = order(
        refunds=[{"transactions": [{"kind": "refund", "status": "success", "amount": "500.00"}]}],
        email="buyer@example.com",
    )
    writer = RealShopifyWriter(
        make_http(lambda r: httpx.Response(200, json={"orders": [raw]})),
        "a.myshopify.com",
        "tok-abcd1234",
    )
    after = await writer.read_order("#1001")
    assert after is not None
    assert after.refunded_paise == 50_000
    assert after.total_paise == 149_950
    assert after.customer_email == "buyer@example.com"
    assert after.customer_phone == "+919876543210"


async def test_get_writer_for_tenant_prefers_override() -> None:
    server = FakeShopifyRest(order())
    override = RealShopifyWriter(make_http(server.handler), "a.myshopify.com", "tok-abcd1234")
    set_writer_override(override)
    try:
        assert (await get_writer_for_tenant(None, uuid.uuid4())) is override  # type: ignore[arg-type]
    finally:
        set_writer_override(None)


async def test_get_writer_for_tenant_disabled_when_not_configured(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    set_writer_override(None)
    email = await active_user(client, mailer, runtime_factory)
    tenant_id = await set_tenant_status(runtime_factory, email, "active")
    writer = await get_writer_for_tenant(runtime_factory, tenant_id)
    with pytest.raises(WriteUnavailable):
        await writer.create_refund("#1001", 100, "k")


async def test_get_writer_for_tenant_builds_real_writer_once_configured(
    client: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    from app.integrations.service import get_transport

    set_writer_override(None)
    upstream = Upstream()
    app = client._transport.app  # type: ignore[attr-defined]
    app.dependency_overrides[get_transport] = lambda: httpx.MockTransport(upstream.handler)
    email = await active_user(client, mailer, runtime_factory)
    headers = bearer(await login(client, email))
    put = await client.put("/integrations/shopify", json=SHOP, headers=headers)
    assert put.status_code == 200, put.text
    tenant_id = await set_tenant_status(runtime_factory, email, "active")
    writer = await get_writer_for_tenant(runtime_factory, tenant_id)
    assert isinstance(writer, RealShopifyWriter)
    app.dependency_overrides.pop(get_transport, None)
