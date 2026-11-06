import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import session_scope
from app.integrations.http import DbCallRecorder, ExternalHttp, UpstreamError
from app.v1.contracts import AddressIn

Outcome = Literal["ok", "rejected", "upstream_error", "unknown"]
Factory = async_sessionmaker[AsyncSession]


class WriteUnavailable(Exception):
    """Raised when no verified Shopify write client is configured. The action goes to needs_review."""


@dataclass(frozen=True)
class WriteResult:
    outcome: Outcome
    external_ref: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class WrittenOrder:
    order_ref: str
    cancelled: bool
    fulfillment_status: str
    refunded_paise: int
    total_paise: int
    address_pin: str | None
    address_line1: str | None
    exchange_requested: bool
    customer_phone: str | None
    customer_email: str | None
    return_requested: bool = False


class ShopifyWriter(Protocol):
    async def read_order(self, order_ref: str) -> WrittenOrder | None: ...

    async def update_address(self, order_ref: str, address: AddressIn, key: str) -> WriteResult: ...

    async def cancel_order(self, order_ref: str, reason: str, key: str) -> WriteResult: ...

    async def create_exchange(
        self, order_ref: str, line_item: str, new_variant: str, key: str
    ) -> WriteResult: ...

    async def create_refund(self, order_ref: str, amount_paise: int, key: str) -> WriteResult: ...

    async def create_return(self, order_ref: str, reason: str, key: str) -> WriteResult:
        """Registers a return request (no refund) - refund_create stays a separate action."""
        ...


class DisabledShopifyWriter:
    """Default until the Shopify write API shapes are verified (DECISIONS: VERIFY)."""

    async def read_order(self, order_ref: str) -> WrittenOrder | None:
        raise WriteUnavailable("shopify write client not verified")

    async def update_address(self, order_ref: str, address: AddressIn, key: str) -> WriteResult:
        raise WriteUnavailable("shopify write client not verified")

    async def cancel_order(self, order_ref: str, reason: str, key: str) -> WriteResult:
        raise WriteUnavailable("shopify write client not verified")

    async def create_exchange(
        self, order_ref: str, line_item: str, new_variant: str, key: str
    ) -> WriteResult:
        raise WriteUnavailable("shopify write client not verified")

    async def create_refund(self, order_ref: str, amount_paise: int, key: str) -> WriteResult:
        raise WriteUnavailable("shopify write client not verified")

    async def create_return(self, order_ref: str, reason: str, key: str) -> WriteResult:
        raise WriteUnavailable("shopify write client not verified")


SHOPIFY_API_VERSION = "2024-10"
EXCHANGE_TAG = "sa-exchange-requested"
RETURN_TAG = "sa-return-requested"


class _Ambiguous(Exception):
    """Internal: ExternalHttp gave up retrying and we don't know if Shopify applied the
    write before failing. Converted to WriteResult('unknown', ...) at the method boundary
    so writes.py's read-before/read-after verify_effect() can resolve the truth."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def _paise(amount: str) -> int:
    whole, _, frac = str(amount).partition(".")
    frac = (frac + "00")[:2]
    return int(whole or "0") * 100 + int(frac)


def _rupees(amount_paise: int) -> str:
    rupees, paise = divmod(max(amount_paise, 0), 100)
    return f"{rupees}.{paise:02d}"


def _tags_of(raw: dict[str, Any]) -> set[str]:
    return {t.strip() for t in str(raw.get("tags") or "").split(",") if t.strip()}


def _idempotency_tag(tool: str, key: str) -> str:
    return f"sa-{tool}-{hashlib.sha256(key.encode()).hexdigest()[:16]}"


def _written_order(order_ref: str, raw: dict[str, Any]) -> WrittenOrder:
    tags = _tags_of(raw)
    refunded = 0
    for refund in raw.get("refunds") or []:
        for txn in refund.get("transactions") or []:
            if str(txn.get("kind")) == "refund" and str(txn.get("status", "success")) == "success":
                refunded += _paise(str(txn.get("amount") or "0"))
    shipping = raw.get("shipping_address") or {}
    customer = raw.get("customer") or {}
    return WrittenOrder(
        order_ref=order_ref,
        cancelled=raw.get("cancelled_at") is not None,
        fulfillment_status=str(raw.get("fulfillment_status") or "unfulfilled"),
        refunded_paise=refunded,
        total_paise=_paise(str(raw.get("total_price") or "0")),
        address_pin=shipping.get("zip"),
        address_line1=shipping.get("address1"),
        exchange_requested=EXCHANGE_TAG in tags,
        customer_phone=raw.get("phone") or customer.get("phone"),
        customer_email=raw.get("email") or customer.get("email"),
        return_requested=RETURN_TAG in tags,
    )


class RealShopifyWriter:
    """Shopify Admin REST API client for the 5 write actions.

    DECISIONS: VERIFY - not exercised against a live store (no sandbox credentials
    available in this environment). cancel_order/create_refund/update_address call
    Shopify's documented single-resource REST endpoints (order cancel, refund via the
    order's original sale/capture transaction, and shipping_address update). Shopify's
    REST Admin API has no single-call equivalent for an arbitrary variant exchange or a
    formal Return object (those live behind the GraphQL Returns API, which is tied to
    fulfillment order line items and a store's configured return rules) - create_exchange
    and create_return instead record the request on the order itself (a tag the agent can
    re-check idempotently, plus a note describing what was asked) so the merchant finishes
    the actual exchange/return in Shopify admin. This mirrors how `exchange_requested` /
    `return_requested` are plain booleans on WrittenOrder rather than real Shopify fields.

    Idempotency: Shopify's REST write endpoints don't accept an idempotency key, so each
    successful write tags the order with a fingerprint of `key`; a retry with the same key
    sees its own tag already there and returns the cached-ok outcome without re-sending the
    request to Shopify.
    """

    def __init__(self, http: ExternalHttp, shop_domain: str, access_token: str) -> None:
        self._http = http
        self._domain = shop_domain
        self._token = access_token

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "X-Shopify-Access-Token": self._token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"https://{self._domain}/admin/api/{SHOPIFY_API_VERSION}/{path}"

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
        name = order_ref if order_ref.startswith("#") else f"#{order_ref}"
        response = await self._request(
            "GET",
            "orders.json",
            operation="order_lookup",
            params={"name": name, "status": "any", "limit": "5"},
        )
        if response.status_code in (401, 403):
            raise WriteUnavailable("shopify auth failed")
        if response.status_code != 200:
            return None
        try:
            orders = response.json().get("orders", [])
        except ValueError:
            return None
        return next((o for o in orders if str(o.get("name")) == name), None)

    async def read_order(self, order_ref: str) -> WrittenOrder | None:
        try:
            raw = await self._find_raw(order_ref)
        except _Ambiguous:
            return None
        return None if raw is None else _written_order(order_ref, raw)

    def _already_done(self, raw: dict[str, Any], tool: str, key: str) -> bool:
        return _idempotency_tag(tool, key) in _tags_of(raw)

    async def _tag(self, order_id: int, raw: dict[str, Any], *extra: str) -> None:
        tags = _tags_of(raw) | set(extra)
        await self._request(
            "PUT",
            f"orders/{order_id}.json",
            operation="order_update",
            json={"order": {"id": order_id, "tags": ", ".join(sorted(tags))}},
        )

    async def update_address(self, order_ref: str, address: AddressIn, key: str) -> WriteResult:
        try:
            raw = await self._find_raw(order_ref)
            if raw is None:
                return WriteResult("rejected", detail="order_not_found")
            order_id = raw["id"]
            if self._already_done(raw, "address_update", key):
                return WriteResult(
                    "ok", external_ref=f"shopify-order-{order_id}", detail="idempotent_replay"
                )
            response = await self._request(
                "PUT",
                f"orders/{order_id}.json",
                operation="address_update",
                json={
                    "order": {
                        "id": order_id,
                        "shipping_address": {
                            "address1": address.line1,
                            "address2": address.line2,
                            "city": address.city,
                            "province": address.state,
                            "zip": address.pin,
                            "country_code": address.country,
                        },
                    }
                },
            )
            if response.status_code in (401, 403):
                raise WriteUnavailable("shopify auth failed")
            if response.status_code >= 400:
                return WriteResult("rejected", detail=f"http_{response.status_code}")
            await self._tag(order_id, raw, _idempotency_tag("address_update", key))
            return WriteResult("ok", external_ref=f"shopify-order-{order_id}")
        except _Ambiguous as exc:
            return WriteResult("unknown", detail=f"upstream:{exc.kind}")

    async def cancel_order(self, order_ref: str, reason: str, key: str) -> WriteResult:
        try:
            raw = await self._find_raw(order_ref)
            if raw is None:
                return WriteResult("rejected", detail="order_not_found")
            order_id = raw["id"]
            if raw.get("cancelled_at") is not None or self._already_done(raw, "order_cancel", key):
                return WriteResult(
                    "ok", external_ref=f"shopify-order-{order_id}", detail="idempotent_replay"
                )
            response = await self._request(
                "POST",
                f"orders/{order_id}/cancel.json",
                operation="order_cancel",
                json={"reason": "customer", "email": False, "note": reason[:200]},
            )
            if response.status_code in (401, 403):
                raise WriteUnavailable("shopify auth failed")
            if response.status_code >= 400:
                return WriteResult("rejected", detail=f"http_{response.status_code}")
            await self._tag(order_id, raw, _idempotency_tag("order_cancel", key))
            return WriteResult("ok", external_ref=f"shopify-order-{order_id}")
        except _Ambiguous as exc:
            return WriteResult("unknown", detail=f"upstream:{exc.kind}")

    async def create_refund(self, order_ref: str, amount_paise: int, key: str) -> WriteResult:
        try:
            raw = await self._find_raw(order_ref)
            if raw is None:
                return WriteResult("rejected", detail="order_not_found")
            order_id = raw["id"]
            if self._already_done(raw, "refund_create", key):
                return WriteResult(
                    "ok", external_ref=f"shopify-order-{order_id}", detail="idempotent_replay"
                )
            txns = await self._request(
                "GET", f"orders/{order_id}/transactions.json", operation="refund_lookup"
            )
            if txns.status_code in (401, 403):
                raise WriteUnavailable("shopify auth failed")
            if txns.status_code != 200:
                return WriteResult("rejected", detail=f"http_{txns.status_code}")
            parent = next(
                (
                    t
                    for t in txns.json().get("transactions", [])
                    if str(t.get("kind")) in ("sale", "capture")
                    and str(t.get("status")) == "success"
                ),
                None,
            )
            if parent is None:
                return WriteResult("rejected", detail="no_capturable_transaction")
            response = await self._request(
                "POST",
                f"orders/{order_id}/refunds.json",
                operation="refund_create",
                json={
                    "refund": {
                        "notify": True,
                        "note": "support-agent refund",
                        "transactions": [
                            {
                                "parent_id": parent["id"],
                                "amount": _rupees(amount_paise),
                                "kind": "refund",
                                "gateway": parent.get("gateway"),
                            }
                        ],
                    }
                },
            )
            if response.status_code in (401, 403):
                raise WriteUnavailable("shopify auth failed")
            if response.status_code >= 400:
                return WriteResult("rejected", detail=f"http_{response.status_code}")
            await self._tag(order_id, raw, _idempotency_tag("refund_create", key))
            refund_id = (response.json().get("refund") or {}).get("id")
            return WriteResult("ok", external_ref=f"shopify-refund-{refund_id}")
        except _Ambiguous as exc:
            return WriteResult("unknown", detail=f"upstream:{exc.kind}")

    async def create_exchange(
        self, order_ref: str, line_item: str, new_variant: str, key: str
    ) -> WriteResult:
        try:
            raw = await self._find_raw(order_ref)
            if raw is None:
                return WriteResult("rejected", detail="order_not_found")
            order_id = raw["id"]
            if EXCHANGE_TAG in _tags_of(raw) or self._already_done(raw, "exchange_request", key):
                return WriteResult(
                    "ok", external_ref=f"shopify-order-{order_id}", detail="idempotent_replay"
                )
            note = (
                f"{raw.get('note') or ''}\nExchange requested via support agent:"
                f" {line_item} -> {new_variant}"
            ).strip()
            response = await self._request(
                "PUT",
                f"orders/{order_id}.json",
                operation="exchange_request",
                json={"order": {"id": order_id, "note": note[:5000]}},
            )
            if response.status_code in (401, 403):
                raise WriteUnavailable("shopify auth failed")
            if response.status_code >= 400:
                return WriteResult("rejected", detail=f"http_{response.status_code}")
            await self._tag(order_id, raw, EXCHANGE_TAG, _idempotency_tag("exchange_request", key))
            return WriteResult("ok", external_ref=f"shopify-order-{order_id}")
        except _Ambiguous as exc:
            return WriteResult("unknown", detail=f"upstream:{exc.kind}")

    async def create_return(self, order_ref: str, reason: str, key: str) -> WriteResult:
        try:
            raw = await self._find_raw(order_ref)
            if raw is None:
                return WriteResult("rejected", detail="order_not_found")
            order_id = raw["id"]
            if RETURN_TAG in _tags_of(raw) or self._already_done(raw, "return_request", key):
                return WriteResult(
                    "ok", external_ref=f"shopify-order-{order_id}", detail="idempotent_replay"
                )
            note = (
                f"{raw.get('note') or ''}\nReturn requested via support agent: {reason}"
            ).strip()
            response = await self._request(
                "PUT",
                f"orders/{order_id}.json",
                operation="return_request",
                json={"order": {"id": order_id, "note": note[:5000]}},
            )
            if response.status_code in (401, 403):
                raise WriteUnavailable("shopify auth failed")
            if response.status_code >= 400:
                return WriteResult("rejected", detail=f"http_{response.status_code}")
            await self._tag(order_id, raw, RETURN_TAG, _idempotency_tag("return_request", key))
            return WriteResult("ok", external_ref=f"shopify-order-{order_id}")
        except _Ambiguous as exc:
            return WriteResult("unknown", detail=f"upstream:{exc.kind}")


ScriptStep = Literal[
    "ok",
    "rejected",
    "upstream_error",
    "timeout_applied",
    "timeout_not_applied",
    "partial",
]


@dataclass
class FakeShopify:
    """In-memory Shopify write server. `script[tool]` is consumed one step per call; empty means ok."""

    orders: dict[str, WrittenOrder] = field(default_factory=dict)
    script: dict[str, list[ScriptStep]] = field(default_factory=dict)
    calls: list[dict[str, object]] = field(default_factory=list)
    seen: dict[str, WriteResult] = field(default_factory=dict)

    def add_order(
        self,
        order_ref: str = "#1001",
        total_paise: int = 149_950,
        fulfillment_status: str = "unfulfilled",
        phone: str | None = "+919876543210",
        email: str | None = None,
    ) -> WrittenOrder:
        order = WrittenOrder(
            order_ref=order_ref,
            cancelled=False,
            fulfillment_status=fulfillment_status,
            refunded_paise=0,
            total_paise=total_paise,
            address_pin="110001",
            address_line1="old line",
            exchange_requested=False,
            customer_phone=phone,
            customer_email=email,
            return_requested=False,
        )
        self.orders[order_ref] = order
        return order

    def _step(self, tool: str) -> ScriptStep:
        queue = self.script.get(tool, [])
        return queue.pop(0) if queue else "ok"

    def _replace(self, order_ref: str, **changes: object) -> None:
        current = self.orders[order_ref]
        self.orders[order_ref] = WrittenOrder(**{**current.__dict__, **changes})

    async def read_order(self, order_ref: str) -> WrittenOrder | None:
        return self.orders.get(order_ref)

    async def _run(
        self,
        tool: str,
        order_ref: str,
        key: str,
        detail: dict[str, object],
        apply: Callable[[bool], None],
    ) -> WriteResult:
        self.calls.append({"tool": tool, "order_ref": order_ref, "key": key, **detail})
        if key in self.seen:
            return self.seen[key]
        if order_ref not in self.orders:
            return WriteResult("rejected", detail="order_not_found")
        step = self._step(tool)
        if step == "rejected":
            return WriteResult("rejected", detail="validation_failed")
        if step == "upstream_error":
            return WriteResult("upstream_error", detail="http_503")
        if step == "timeout_not_applied":
            return WriteResult("unknown", detail="timeout")
        apply(step == "partial")
        result = (
            WriteResult("unknown", detail="timeout")
            if step == "timeout_applied"
            else WriteResult("ok", external_ref=f"fake-{tool}-{len(self.calls)}")
        )
        self.seen[key] = result
        return result

    async def update_address(self, order_ref: str, address: AddressIn, key: str) -> WriteResult:
        def apply(partial: bool) -> None:
            self._replace(order_ref, address_pin=address.pin, address_line1=address.line1)

        return await self._run("address_update", order_ref, key, {"pin": address.pin}, apply)

    async def cancel_order(self, order_ref: str, reason: str, key: str) -> WriteResult:
        def apply(partial: bool) -> None:
            self._replace(order_ref, cancelled=True)

        return await self._run("order_cancel", order_ref, key, {"reason": reason}, apply)

    async def create_exchange(
        self, order_ref: str, line_item: str, new_variant: str, key: str
    ) -> WriteResult:
        def apply(partial: bool) -> None:
            self._replace(order_ref, exchange_requested=True)

        return await self._run(
            "exchange_request",
            order_ref,
            key,
            {"line_item": line_item, "variant": new_variant},
            apply,
        )

    async def create_refund(self, order_ref: str, amount_paise: int, key: str) -> WriteResult:
        def apply(partial: bool) -> None:
            applied = amount_paise // 2 if partial else amount_paise
            self._replace(order_ref, refunded_paise=self.orders[order_ref].refunded_paise + applied)

        return await self._run(
            "refund_create", order_ref, key, {"amount_paise": amount_paise}, apply
        )

    async def create_return(self, order_ref: str, reason: str, key: str) -> WriteResult:
        def apply(partial: bool) -> None:
            self._replace(order_ref, return_requested=True)

        return await self._run("return_request", order_ref, key, {"reason": reason}, apply)


_override: ShopifyWriter | None = None


def set_writer_override(writer: ShopifyWriter | None) -> None:
    global _override
    _override = writer


def get_writer() -> ShopifyWriter:
    return _override if _override is not None else DisabledShopifyWriter()


async def get_writer_for_tenant(
    factory: Factory, tenant_id: uuid.UUID, transport: httpx.AsyncBaseTransport | None = None
) -> ShopifyWriter:
    """Production entry point for the 3 call sites that execute confirmed write actions
    (runner_v1._confirm_yes, approvals.approve_action/reconcile_action, jobs.ops.write_upkeep).

    Returns the test override first (get_writer()'s existing behaviour, unchanged), so
    every test that calls set_writer_override() keeps working without modification. With
    no override, it loads the tenant's saved Shopify credentials the same way the read-only
    ShopifyClient does (app.integrations.service.load_secret) and returns a RealShopifyWriter,
    or DisabledShopifyWriter when the tenant hasn't configured Shopify - identical to today's
    behaviour for those tenants.
    """
    if _override is not None:
        return _override
    from app.integrations.service import load_secret

    async with session_scope(factory, tenant_id=tenant_id) as session:
        loaded = await load_secret(session, "shopify")
    if loaded is None:
        return DisabledShopifyWriter()
    config, secret = loaded
    http = ExternalHttp("shopify", recorder=DbCallRecorder(factory, tenant_id), transport=transport)
    return RealShopifyWriter(http, config["shop_domain"], secret["access_token"])


def verify_effect(
    tool: str,
    before_refunded: int,
    after: WrittenOrder,
    *,
    amount_paise: int | None = None,
    address_pin: str | None = None,
) -> bool:
    if tool == "order_cancel":
        return after.cancelled
    if tool == "address_update":
        return address_pin is not None and after.address_pin == address_pin
    if tool == "exchange_request":
        return after.exchange_requested
    if tool == "refund_create":
        return amount_paise is not None and after.refunded_paise - before_refunded == amount_paise
    if tool == "return_request":
        return after.return_requested
    return False
