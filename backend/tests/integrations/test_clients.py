import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.integrations.courier import DelhiveryClient, ShiprocketClient, clear_token_cache
from app.integrations.http import CallRecord, ExternalHttp, UpstreamError
from app.integrations.shopify import ShopifyClient, customer_matches, phones_match

Handler = Callable[[httpx.Request], httpx.Response]


async def _nosleep(_: float) -> None:
    return None


def make_http(handler: Handler, records: list[CallRecord] | None = None) -> ExternalHttp:
    async def recorder(record: CallRecord) -> None:
        if records is not None:
            records.append(record)

    return ExternalHttp(
        "test", recorder=recorder, transport=httpx.MockTransport(handler), sleep=_nosleep
    )


ORDER: dict[str, Any] = {
    "name": "#1001",
    "financial_status": "paid",
    "fulfillment_status": "fulfilled",
    "total_price": "1499.50",
    "created_at": "2026-03-01T10:00:00+05:30",
    "phone": "+919876543210",
    "email": "Buyer@Example.com",
    "line_items": [{"title": "Kurta", "quantity": 2}],
    "fulfillments": [{"tracking_numbers": ["AWB1"], "tracking_company": "Delhivery"}],
}


def shopify_handler(orders: list[dict[str, Any]], status: int = 200) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-shopify-access-token"] == "tok-123456789"
        return httpx.Response(status, json={"orders": orders})

    return handler


async def test_shopify_ok_money_in_paise() -> None:
    client = ShopifyClient(make_http(shopify_handler([ORDER])), "a.myshopify.com", "tok-123456789")
    result = await client.lookup_order("1001", "+919876543210", None)
    assert result.ok and result.order is not None
    assert result.order.total_paise == 149950
    assert result.order.tracking_numbers == ["AWB1"] and result.order.courier == "Delhivery"
    assert result.order.lines[0].quantity == 2


async def test_shopify_email_match_and_mismatch_fail_closed() -> None:
    client = ShopifyClient(make_http(shopify_handler([ORDER])), "a.myshopify.com", "tok-123456789")
    assert (await client.lookup_order("#1001", None, " buyer@example.com ")).ok
    for phone, email in (("+911111111111", None), (None, "x@example.com"), (None, None)):
        result = await client.lookup_order("#1001", phone, email)
        assert not result.ok and result.error_code == "customer_mismatch"


async def test_shopify_error_mapping() -> None:
    def make(handler: Handler) -> ShopifyClient:
        return ShopifyClient(make_http(handler), "a.myshopify.com", "tok-123456789")

    nf = await make(shopify_handler([])).lookup_order("1001", "+919876543210", None)
    assert nf.error_code == "not_found"
    auth = await make(shopify_handler([], 401)).lookup_order("1001", "+919876543210", None)
    assert auth.error_code == "auth_error"
    other = await make(shopify_handler([ORDER], 404)).lookup_order("1001", "+919876543210", None)
    assert other.error_code == "upstream_error"
    wrong = await make(shopify_handler([{**ORDER, "name": "#2"}])).lookup_order(
        "1001", "+919876543210", None
    )
    assert wrong.error_code == "not_found"
    broken = await make(
        shopify_handler([{"name": "#1001", "phone": "+919876543210"}])
    ).lookup_order("1001", "+919876543210", None)
    assert broken.error_code == "upstream_error"


def test_phone_matching() -> None:
    assert phones_match("+919876543210", "98765 43210")
    assert not phones_match("+919876543210", "123")
    assert not customer_matches({"phone": "+919876543210"}, None, None)


async def test_retry_on_503_then_success_and_records_each_attempt() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503 if calls["n"] < 3 else 200, json={"ok": True})

    records: list[CallRecord] = []
    response = await make_http(handler, records).request(
        "GET", "https://x.example/p", operation="o"
    )
    assert response.status_code == 200 and calls["n"] == 3
    assert [r.http_status for r in records] == [503, 503, 200]
    assert records[0].error_class == "http_503"


async def test_429_uses_retry_after_and_gives_up() -> None:
    delays: list[float] = []

    async def sleep(value: float) -> None:
        delays.append(value)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "2"})

    http = ExternalHttp("t", transport=httpx.MockTransport(handler), sleep=sleep)
    with pytest.raises(UpstreamError) as info:
        await http.request("GET", "https://x.example/p", operation="o")
    assert info.value.kind == "rate_limited" and delays == [2.0, 2.0]


async def test_transport_error_and_timeout_raise_upstream() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no")

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    for handler, kind in ((boom, "ConnectError"), (slow, "timeout")):
        with pytest.raises(UpstreamError) as info:
            await make_http(handler).request("GET", "https://x.example/p", operation="o")
        assert info.value.kind == kind


async def test_client_error_not_retried_body_redacted_and_capped() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, text="bad mail a.b@example.com " + "x" * 5000)

    records: list[CallRecord] = []
    response = await make_http(handler, records).request(
        "GET", "https://x.example/p", operation="o"
    )
    assert response.status_code == 400 and calls["n"] == 1
    body = records[0].response_body_redacted
    assert body is not None and len(body) <= 2048 and "a.b@example.com" not in body


async def test_success_body_not_stored() -> None:
    records: list[CallRecord] = []
    http = make_http(lambda r: httpx.Response(200, json={"secret": "v"}), records)
    await http.request("GET", "https://x.example/p", operation="o")
    assert records[0].response_body_redacted is None


async def test_delhivery_track() -> None:
    payload = {
        "ShipmentData": [
            {
                "Shipment": {
                    "Status": {"Status": "In Transit", "StatusDateTime": "2026-03-02T10:00:00"},
                    "ExpectedDeliveryDate": "2026-03-05T00:00:00",
                }
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Token tk-1234567"
        assert request.url.params["waybill"] == "AWB1"
        return httpx.Response(200, json=payload)

    result = await DelhiveryClient(make_http(handler), "tk-1234567").track("AWB1")
    assert result.ok and result.tracking is not None
    assert result.tracking.status_text == "In Transit" and result.tracking.courier == "Delhivery"
    empty = await DelhiveryClient(
        make_http(lambda r: httpx.Response(200, json={"ShipmentData": []})), "tk-1234567"
    ).track("AWB1")
    assert empty.error_code == "not_found"
    auth = await DelhiveryClient(make_http(lambda r: httpx.Response(401)), "tk").track("A")
    assert auth.error_code == "auth_error"
    bad = await DelhiveryClient(
        make_http(lambda r: httpx.Response(200, json={"ShipmentData": [{}]})), "tk"
    ).track("A")
    assert bad.error_code == "upstream_error"


async def test_shiprocket_login_cached_and_relogin_on_401() -> None:
    clear_token_cache()
    state = {"logins": 0, "tracks": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            state["logins"] += 1
            body = json.loads(request.content)
            assert body == {"email": "a@example.com", "password": "pw123"}
            return httpx.Response(200, json={"token": f"t{state['logins']}"})
        state["tracks"] += 1
        if request.headers["authorization"] == "Bearer t1" and state["tracks"] == 2:
            return httpx.Response(401)
        return httpx.Response(
            200,
            json={
                "tracking_data": {
                    "track_status": 1,
                    "etd": "2026-03-05 10:00:00",
                    "shipment_track": [{"courier_name": "Xpress", "current_status": "Delivered"}],
                }
            },
        )

    tenant = uuid.uuid4()
    client = ShiprocketClient(make_http(handler), tenant, "a@example.com", "pw123")
    first = await client.track("A1")
    assert first.ok and first.tracking is not None and first.tracking.courier == "Xpress"
    await client.track("A1")
    assert state["logins"] == 2
    clear_token_cache()


async def test_shiprocket_auth_failure_and_not_found() -> None:
    clear_token_cache()
    bad_login = ShiprocketClient(
        make_http(lambda r: httpx.Response(403)), uuid.uuid4(), "a@example.com", "pw"
    )
    assert (await bad_login.track("A")).error_code == "auth_error"
    assert not await bad_login.probe()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"token": "t"})
        return httpx.Response(200, json={"tracking_data": {"track_status": 0}})

    client = ShiprocketClient(make_http(handler), uuid.uuid4(), "a@example.com", "pw")
    assert (await client.track("A")).error_code == "not_found"
    clear_token_cache()
