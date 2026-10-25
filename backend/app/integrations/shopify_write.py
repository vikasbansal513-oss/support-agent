from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from app.v1.contracts import AddressIn

Outcome = Literal["ok", "rejected", "upstream_error", "unknown"]


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


class ShopifyWriter(Protocol):
    async def read_order(self, order_ref: str) -> WrittenOrder | None: ...

    async def update_address(self, order_ref: str, address: AddressIn, key: str) -> WriteResult: ...

    async def cancel_order(self, order_ref: str, reason: str, key: str) -> WriteResult: ...

    async def create_exchange(
        self, order_ref: str, line_item: str, new_variant: str, key: str
    ) -> WriteResult: ...

    async def create_refund(self, order_ref: str, amount_paise: int, key: str) -> WriteResult: ...


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


_override: ShopifyWriter | None = None


def set_writer_override(writer: ShopifyWriter | None) -> None:
    global _override
    _override = writer


def get_writer() -> ShopifyWriter:
    return _override if _override is not None else DisabledShopifyWriter()


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
    return False
