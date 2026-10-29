import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from app.contracts.contracts import ToolResult
from app.integrations.courier import CourierClient
from app.integrations.shopify import ShopifyClient

ToolFn = Callable[..., Awaitable[ToolResult]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    risk: Literal["low", "high"]
    needs_customer_match: bool


REGISTRY: dict[str, ToolSpec] = {
    "order_lookup": ToolSpec("order_lookup", "low", True),
    "tracking_lookup": ToolSpec("tracking_lookup", "low", False),
    "handoff_to_human": ToolSpec("handoff_to_human", "low", False),
}


class ToolBox(Protocol):
    calls: list[dict[str, Any]]

    async def order_lookup(
        self, order_ref: str, phone_e164: str | None, email: str | None
    ) -> ToolResult: ...

    async def tracking_lookup(self, awb: str, courier_hint: str | None) -> ToolResult: ...


@dataclass
class LiveToolBox:
    tenant_id: uuid.UUID
    shopify: ShopifyClient | None
    couriers: dict[str, CourierClient]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def _log(self, tool: str, args: dict[str, Any], result: ToolResult) -> ToolResult:
        self.calls.append({"tool": tool, "args": args, "ok": result.ok, "error": result.error_code})
        return result

    async def order_lookup(
        self, order_ref: str, phone_e164: str | None, email: str | None
    ) -> ToolResult:
        if self.shopify is None:
            return self._log(
                "order_lookup",
                {"order_ref": order_ref},
                ToolResult(tool="order_lookup", ok=False, error_code="not_configured"),
            )
        result = await self.shopify.lookup_order(order_ref, phone_e164, email)
        return self._log("order_lookup", {"order_ref": order_ref}, result)

    async def tracking_lookup(self, awb: str, courier_hint: str | None) -> ToolResult:
        client = self._pick(courier_hint)
        if client is None:
            return self._log(
                "tracking_lookup",
                {"awb": awb},
                ToolResult(tool="tracking_lookup", ok=False, error_code="not_configured"),
            )
        return self._log("tracking_lookup", {"awb": awb}, await client.track(awb))

    def _pick(self, courier_hint: str | None) -> CourierClient | None:
        # Normalize to letters/digits only so multi-word courier names
        # ("Ecom Express" -> kind "ecom_express") still match the hint
        # Shopify gives, regardless of spaces/underscores/case.
        hint = "".join(ch for ch in (courier_hint or "").lower() if ch.isalnum())
        for kind, client in self.couriers.items():
            normalized_kind = "".join(ch for ch in kind.lower() if ch.isalnum())
            if normalized_kind and normalized_kind in hint:
                return client
        if len(self.couriers) == 1:
            return next(iter(self.couriers.values()))
        return None


@dataclass
class FakeToolBox:
    orders: dict[str, ToolResult] = field(default_factory=dict)
    tracking: dict[str, ToolResult] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def order_lookup(
        self, order_ref: str, phone_e164: str | None, email: str | None
    ) -> ToolResult:
        self.calls.append(
            {"tool": "order_lookup", "args": {"order_ref": order_ref}, "ok": True, "error": None}
        )
        name = order_ref if order_ref.startswith("#") else f"#{order_ref}"
        return self.orders.get(
            name, ToolResult(tool="order_lookup", ok=False, error_code="not_found")
        )

    async def tracking_lookup(self, awb: str, courier_hint: str | None) -> ToolResult:
        self.calls.append(
            {"tool": "tracking_lookup", "args": {"awb": awb}, "ok": True, "error": None}
        )
        return self.tracking.get(
            awb, ToolResult(tool="tracking_lookup", ok=False, error_code="not_found")
        )
