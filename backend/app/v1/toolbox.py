from dataclasses import dataclass
from typing import Protocol

from app.agent.tools import LiveToolBox, ToolBox
from app.contracts.contracts import ToolResult
from app.v1.gate import GateOrder


class V1ToolBox(ToolBox, Protocol):
    async def order_gate(
        self, order_ref: str, phone_e164: str | None, email: str | None
    ) -> tuple[ToolResult, GateOrder | None]: ...


@dataclass
class LiveV1ToolBox(LiveToolBox):
    async def order_gate(
        self, order_ref: str, phone_e164: str | None, email: str | None
    ) -> tuple[ToolResult, GateOrder | None]:
        if self.shopify is None:
            result = ToolResult(tool="order_lookup", ok=False, error_code="not_configured")
            return self._log("order_lookup", {"order_ref": order_ref}, result), None
        result, gate = await self.shopify.lookup_order_gate(order_ref, phone_e164, email)
        return self._log("order_lookup", {"order_ref": order_ref}, result), gate


class ToolBoxFacts:
    """GateFacts for a verified customer: the same customer-match rule as the first lookup."""

    def __init__(self, box: V1ToolBox, phone_e164: str | None, email: str | None) -> None:
        self._box, self._phone, self._email = box, phone_e164, email

    async def order(self, order_ref: str) -> GateOrder | None:
        _, gate = await self._box.order_gate(order_ref, self._phone, self._email)
        return gate
