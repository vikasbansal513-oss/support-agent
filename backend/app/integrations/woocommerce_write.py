from typing import Any

import httpx

from app.integrations.http import ExternalHttp, UpstreamError
from app.integrations.shopify_write import (
    WriteResult,
    WriteUnavailable,
    WrittenOrder,
    _Ambiguous,
    _idempotency_tag,
    _rupees,
)
from app.integrations.woocommerce import (
    API_PREFIX,
    CANCELLED_STATUSES,
    basic_auth_header,
    meta_keys,
    order_id_of,
    paise,
    refunded_paise,
)
from app.v1.contracts import AddressIn

EXCHANGE_META = "_sa_exchange_requested"
RETURN_META = "_sa_return_requested"


def _idem_meta(tool: str, key: str) -> str:
    return f"_{_idempotency_tag(tool, key)}"


def _meta(*keys: str) -> list[dict[str, str]]:
    return [{"key": k, "value": "1"} for k in keys]


def _written_order(order_ref: str, raw: dict[str, Any]) -> WrittenOrder:
    shipping = raw.get("shipping") or {}
    billing = raw.get("billing") or {}
    keys = meta_keys(raw)
    return WrittenOrder(
        order_ref=order_ref,
        cancelled=str(raw.get("status")) in CANCELLED_STATUSES,
        fulfillment_status="fulfilled" if str(raw.get("status")) == "completed" else "unfulfilled",
        refunded_paise=refunded_paise(raw),
        total_paise=paise(raw.get("total")),
        address_pin=shipping.get("postcode"),
        address_line1=shipping.get("address_1"),
        exchange_requested=EXCHANGE_META in keys,
        customer_phone=billing.get("phone") or shipping.get("phone"),
        customer_email=billing.get("email"),
        return_requested=RETURN_META in keys,
    )


class RealWooCommerceWriter:
    """WooCommerce REST v3 client for the 5 write actions, same interface as RealShopifyWriter.

    DECISIONS: VERIFY - not exercised against a live store. cancel = order status 'cancelled'
    (no automatic refund, like the Shopify cancel). refund = POST /orders/{id}/refunds with
    api_refund=true, so the payment gateway plugin must support refunds; if it does not,
    WooCommerce answers with an error and the action ends as rejected. update_address rewrites
    the order's shipping fields (state is sent as given; WooCommerce stores Indian states as
    codes such as 'KL'). WooCommerce has no exchange or return object, so create_exchange and
    create_return only record the request on the order (a hidden meta flag plus a private order
    note) and the merchant finishes it in wp-admin.

    Idempotency: WooCommerce has no idempotency key, so each successful write stores a hidden
    order meta field fingerprinting `key`; a retry with the same key sees it and returns the
    cached-ok outcome without sending the write again. For cancel and address change the meta
    is written in the same request as the change itself.
    """

    def __init__(
        self, http: ExternalHttp, store_url: str, consumer_key: str, consumer_secret: str
    ) -> None:
        self._http = http
        self._base = store_url.rstrip("/")
        self._auth = basic_auth_header(consumer_key, consumer_secret)

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._auth,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{self._base}/{API_PREFIX}/{path}"

    async def _request(
        self, method: str, path: str, *, operation: str, **kwargs: Any
    ) -> httpx.Response:
        try:
            return await self._http.request(
                method, self._url(path), operation=operation, headers=self._headers, **kwargs
            )
        except UpstreamError as exc:
            raise _Ambiguous(exc.kind) from exc

    async def _find_raw(self, order_ref: str) -> dict[str, Any] | None:
        number = order_id_of(order_ref)
        if not number:
            return None
        response = await self._request("GET", f"orders/{number}", operation="order_lookup")
        if response.status_code in (401, 403):
            raise WriteUnavailable("woocommerce auth failed")
        try:
            if response.status_code == 200:
                payload = response.json()
                return payload if isinstance(payload, dict) else None
            if response.status_code != 404:
                return None
            response = await self._request(
                "GET",
                "orders",
                operation="order_lookup",
                params={"search": number, "per_page": "5"},
            )
            if response.status_code != 200:
                return None
            rows = response.json()
        except ValueError:
            return None
        if not isinstance(rows, list):
            return None
        return next(
            (o for o in rows if isinstance(o, dict) and str(o.get("number")) == number), None
        )

    async def read_order(self, order_ref: str) -> WrittenOrder | None:
        try:
            raw = await self._find_raw(order_ref)
        except _Ambiguous:
            return None
        return None if raw is None else _written_order(order_ref, raw)

    @staticmethod
    def _done(raw: dict[str, Any], tool: str, key: str) -> bool:
        return _idem_meta(tool, key) in meta_keys(raw)

    @staticmethod
    def _ref(raw: dict[str, Any]) -> str:
        return f"woocommerce-order-{raw['id']}"

    async def _update(
        self, raw: dict[str, Any], body: dict[str, Any], operation: str
    ) -> WriteResult | None:
        """PUT the order. Returns a WriteResult only when the call failed."""
        response = await self._request("PUT", f"orders/{raw['id']}", operation=operation, json=body)
        if response.status_code in (401, 403):
            raise WriteUnavailable("woocommerce auth failed")
        if response.status_code >= 400:
            return WriteResult("rejected", detail=f"http_{response.status_code}")
        return None

    async def _note(self, raw: dict[str, Any], text: str) -> None:
        try:
            await self._request(
                "POST",
                f"orders/{raw['id']}/notes",
                operation="order_note",
                json={"note": text[:1000], "customer_note": False},
            )
        except _Ambiguous:
            return

    async def update_address(self, order_ref: str, address: AddressIn, key: str) -> WriteResult:
        try:
            raw = await self._find_raw(order_ref)
            if raw is None:
                return WriteResult("rejected", detail="order_not_found")
            if self._done(raw, "address_update", key):
                return WriteResult("ok", external_ref=self._ref(raw), detail="idempotent_replay")
            failed = await self._update(
                raw,
                {
                    "shipping": {
                        "address_1": address.line1,
                        "address_2": address.line2 or "",
                        "city": address.city,
                        "state": address.state,
                        "postcode": address.pin,
                        "country": address.country,
                    },
                    "meta_data": _meta(_idem_meta("address_update", key)),
                },
                "address_update",
            )
            return failed or WriteResult("ok", external_ref=self._ref(raw))
        except _Ambiguous as exc:
            return WriteResult("unknown", detail=f"upstream:{exc.kind}")

    async def cancel_order(self, order_ref: str, reason: str, key: str) -> WriteResult:
        try:
            raw = await self._find_raw(order_ref)
            if raw is None:
                return WriteResult("rejected", detail="order_not_found")
            if str(raw.get("status")) == "cancelled" or self._done(raw, "order_cancel", key):
                return WriteResult("ok", external_ref=self._ref(raw), detail="idempotent_replay")
            failed = await self._update(
                raw,
                {"status": "cancelled", "meta_data": _meta(_idem_meta("order_cancel", key))},
                "order_cancel",
            )
            if failed is not None:
                return failed
            await self._note(raw, f"Cancelled via support agent: {reason}")
            return WriteResult("ok", external_ref=self._ref(raw))
        except _Ambiguous as exc:
            return WriteResult("unknown", detail=f"upstream:{exc.kind}")

    async def create_refund(self, order_ref: str, amount_paise: int, key: str) -> WriteResult:
        try:
            raw = await self._find_raw(order_ref)
            if raw is None:
                return WriteResult("rejected", detail="order_not_found")
            if self._done(raw, "refund_create", key):
                return WriteResult("ok", external_ref=self._ref(raw), detail="idempotent_replay")
            response = await self._request(
                "POST",
                f"orders/{raw['id']}/refunds",
                operation="refund_create",
                json={
                    "amount": _rupees(amount_paise),
                    "reason": "support-agent refund",
                    "api_refund": True,
                },
            )
            if response.status_code in (401, 403):
                raise WriteUnavailable("woocommerce auth failed")
            if response.status_code >= 400:
                return WriteResult("rejected", detail=f"http_{response.status_code}")
            try:
                refund_id = response.json().get("id")
            except ValueError:
                refund_id = None
            await self._update(
                raw, {"meta_data": _meta(_idem_meta("refund_create", key))}, "refund_mark"
            )
            return WriteResult("ok", external_ref=f"woocommerce-refund-{refund_id}")
        except _Ambiguous as exc:
            return WriteResult("unknown", detail=f"upstream:{exc.kind}")

    async def create_exchange(
        self, order_ref: str, line_item: str, new_variant: str, key: str
    ) -> WriteResult:
        try:
            raw = await self._find_raw(order_ref)
            if raw is None:
                return WriteResult("rejected", detail="order_not_found")
            if EXCHANGE_META in meta_keys(raw) or self._done(raw, "exchange_request", key):
                return WriteResult("ok", external_ref=self._ref(raw), detail="idempotent_replay")
            failed = await self._update(
                raw,
                {"meta_data": _meta(EXCHANGE_META, _idem_meta("exchange_request", key))},
                "exchange_request",
            )
            if failed is not None:
                return failed
            await self._note(
                raw, f"Exchange requested via support agent: {line_item} -> {new_variant}"
            )
            return WriteResult("ok", external_ref=self._ref(raw))
        except _Ambiguous as exc:
            return WriteResult("unknown", detail=f"upstream:{exc.kind}")

    async def create_return(self, order_ref: str, reason: str, key: str) -> WriteResult:
        try:
            raw = await self._find_raw(order_ref)
            if raw is None:
                return WriteResult("rejected", detail="order_not_found")
            if RETURN_META in meta_keys(raw) or self._done(raw, "return_request", key):
                return WriteResult("ok", external_ref=self._ref(raw), detail="idempotent_replay")
            failed = await self._update(
                raw,
                {"meta_data": _meta(RETURN_META, _idem_meta("return_request", key))},
                "return_request",
            )
            if failed is not None:
                return failed
            await self._note(raw, f"Return requested via support agent: {reason}")
            return WriteResult("ok", external_ref=self._ref(raw))
        except _Ambiguous as exc:
            return WriteResult("unknown", detail=f"upstream:{exc.kind}")
