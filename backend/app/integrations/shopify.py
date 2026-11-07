import re
from datetime import UTC, datetime
from typing import Any

from app.contracts.contracts import E164_RE, OrderFacts, OrderLine, ToolResult
from app.integrations.http import ExternalHttp, UpstreamError
from app.v1.gate import GateOrder

API_VERSION = "2024-10"


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def phones_match(a: str | None, b: str | None) -> bool:
    da, db = _digits(a), _digits(b)
    if len(da) < 10 or len(db) < 10:
        return False
    return da[-10:] == db[-10:]


def customer_matches(order: dict[str, Any], phone_e164: str | None, email: str | None) -> bool:
    if not phone_e164 and not email:
        return False
    customer = order.get("customer") or {}
    shipping = order.get("shipping_address") or {}
    billing = order.get("billing_address") or {}
    if phone_e164:
        for candidate in (
            order.get("phone"),
            customer.get("phone"),
            shipping.get("phone"),
            billing.get("phone"),
        ):
            if phones_match(phone_e164, candidate):
                return True
    if email:
        wanted = email.strip().lower()
        for candidate in (order.get("email"), order.get("contact_email"), customer.get("email")):
            if isinstance(candidate, str) and candidate.strip().lower() == wanted:
                return True
    return False


def _paise(amount: str) -> int:
    whole, _, frac = str(amount).partition(".")
    frac = (frac + "00")[:2]
    return int(whole or "0") * 100 + int(frac)


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _customer_name(order: dict[str, Any]) -> str | None:
    """Best-effort display name for the order's "Hi {name}," email greeting. Shopify order
    shape is not verified against a real account (VERIFY, same as the rest of this module) -
    tries the customer object, then falls back to the shipping/billing address name. Returns
    None (greeting falls back to "Hi there,") if nothing usable is present."""
    for source in (order.get("customer") or {}, order.get("shipping_address") or {}):
        first = str(source.get("first_name") or "").strip()
        last = str(source.get("last_name") or "").strip()
        full = f"{first} {last}".strip()
        if full:
            return full[:200]
    for source in (order.get("shipping_address") or {}, order.get("billing_address") or {}):
        name = str(source.get("name") or "").strip()
        if name:
            return name[:200]
    return None


def _customer_contact(order: dict[str, Any]) -> tuple[str | None, str | None]:
    """Best-effort E.164 phone + email for this order's customer, used only for omnichannel
    identity-linking (app.channels.identity.link_customer_identities) - never shown to the
    customer. VERIFY, same as the rest of this module: real Shopify phone formatting isn't
    checked against a live account. Accepts a phone only if it's already a valid E.164
    string, or exactly 10 digits (assumed a Kerala/India mobile number, given this product's
    market - prefixed +91); anything else is dropped rather than guessed, since a wrong
    phone would wrongly merge two different customers."""
    customer = order.get("customer") or {}
    shipping = order.get("shipping_address") or {}
    billing = order.get("billing_address") or {}
    phone = None
    for candidate in (order.get("phone"), customer.get("phone"), shipping.get("phone"), billing.get("phone")):
        if not candidate:
            continue
        raw = str(candidate).strip()
        if E164_RE.match(raw):
            phone = raw
            break
        digits = _digits(raw)
        if len(digits) == 10:
            phone = f"+91{digits}"
            break
    email = None
    for candidate in (order.get("email"), order.get("contact_email"), customer.get("email")):
        if isinstance(candidate, str) and candidate.strip():
            email = candidate.strip().lower()[:200]
            break
    return phone, email


def to_facts(order: dict[str, Any]) -> OrderFacts:
    contact_phone, contact_email = _customer_contact(order)
    fulfillments = order.get("fulfillments") or []
    numbers: list[str] = []
    courier: str | None = None
    for fulfillment in fulfillments:
        for number in fulfillment.get("tracking_numbers") or []:
            if number and number not in numbers:
                numbers.append(str(number))
        if courier is None and fulfillment.get("tracking_company"):
            courier = str(fulfillment["tracking_company"])
    return OrderFacts(
        order_name=str(order["name"])[:40],
        financial_status=str(order.get("financial_status") or "pending")[:40],
        fulfillment_status=str(order.get("fulfillment_status") or "unfulfilled")[:40],
        lines=[
            OrderLine(
                title=str(line.get("title") or "item")[:200],
                quantity=max(1, min(int(line.get("quantity") or 1), 10000)),
                unit_price_paise=_paise(str(line.get("price") or "0")),
            )
            for line in (order.get("line_items") or [])[:50]
        ],
        total_paise=_paise(str(order.get("total_price") or "0")),
        created_at=_parse_dt(str(order["created_at"])),
        tracking_numbers=numbers[:10],
        courier=courier,
        customer_name=_customer_name(order),
        customer_phone=contact_phone,
        customer_email=contact_email,
    )


DISPUTE_TAGS = frozenset({"dispute", "disputed", "chargeback"})


def to_gate_order(order: dict[str, Any]) -> GateOrder:
    """VERIFY: delivered_at (fulfillment shipment_status/updated_at), refund transactions and
    dispute tags are assumed from the REST order shape; real Shopify responses are not checked."""
    refunded = 0
    for refund in order.get("refunds") or []:
        for txn in refund.get("transactions") or []:
            if str(txn.get("kind")) == "refund":
                refunded += _paise(str(txn.get("amount") or "0"))
    delivered: datetime | None = None
    for fulfillment in order.get("fulfillments") or []:
        if str(fulfillment.get("shipment_status")) == "delivered" and fulfillment.get("updated_at"):
            delivered = _parse_dt(str(fulfillment["updated_at"]))
    tags = tuple(
        sorted({t.strip().lower() for t in str(order.get("tags") or "").split(",") if t.strip()})
    )
    return GateOrder(
        order_ref=str(order["name"])[:40],
        total_paise=_paise(str(order.get("total_price") or "0")),
        fulfillment_status=str(order.get("fulfillment_status") or "unfulfilled")[:40],
        financial_status=str(order.get("financial_status") or "pending")[:40],
        cancelled=order.get("cancelled_at") is not None,
        refunded_paise=refunded,
        delivered_at=delivered,
        tags=tags,
        disputed=bool(DISPUTE_TAGS & set(tags)),
    )


class ShopifyClient:
    def __init__(self, http: ExternalHttp, shop_domain: str, access_token: str) -> None:
        self._http = http
        self._domain = shop_domain
        self._token = access_token

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Shopify-Access-Token": self._token, "Accept": "application/json"}

    async def probe(self) -> ToolResult | None:
        try:
            response = await self._http.request(
                "GET",
                f"https://{self._domain}/admin/api/{API_VERSION}/shop.json",
                operation="shop",
                headers=self._headers,
            )
        except UpstreamError:
            return ToolResult(tool="order_lookup", ok=False, error_code="upstream_error")
        if response.status_code in (401, 403):
            return ToolResult(tool="order_lookup", ok=False, error_code="auth_error")
        if response.status_code != 200:
            return ToolResult(tool="order_lookup", ok=False, error_code="upstream_error")
        return None

    async def _find(
        self, order_ref: str, phone_e164: str | None, email: str | None
    ) -> tuple[ToolResult | None, dict[str, Any] | None]:
        name = order_ref if order_ref.startswith("#") else f"#{order_ref}"
        try:
            response = await self._http.request(
                "GET",
                f"https://{self._domain}/admin/api/{API_VERSION}/orders.json",
                operation="order_lookup",
                headers=self._headers,
                params={"name": name, "status": "any", "limit": "5"},
            )
        except UpstreamError:
            return ToolResult(tool="order_lookup", ok=False, error_code="upstream_error"), None
        if response.status_code in (401, 403):
            return ToolResult(tool="order_lookup", ok=False, error_code="auth_error"), None
        if response.status_code != 200:
            return ToolResult(tool="order_lookup", ok=False, error_code="upstream_error"), None
        try:
            orders = response.json().get("orders", [])
        except ValueError:
            return ToolResult(tool="order_lookup", ok=False, error_code="upstream_error"), None
        match = next((o for o in orders if str(o.get("name")) == name), None)
        if match is None:
            return ToolResult(tool="order_lookup", ok=False, error_code="not_found"), None
        if not customer_matches(match, phone_e164, email):
            return ToolResult(tool="order_lookup", ok=False, error_code="customer_mismatch"), None
        return None, match

    async def lookup_order(
        self, order_ref: str, phone_e164: str | None, email: str | None
    ) -> ToolResult:
        failure, match = await self._find(order_ref, phone_e164, email)
        if failure is not None or match is None:
            return failure or ToolResult(tool="order_lookup", ok=False, error_code="not_found")
        try:
            return ToolResult(tool="order_lookup", ok=True, order=to_facts(match))
        except (KeyError, ValueError, TypeError):
            return ToolResult(tool="order_lookup", ok=False, error_code="upstream_error")

    async def lookup_order_gate(
        self, order_ref: str, phone_e164: str | None, email: str | None
    ) -> tuple[ToolResult, GateOrder | None]:
        failure, match = await self._find(order_ref, phone_e164, email)
        if failure is not None or match is None:
            return failure or ToolResult(
                tool="order_lookup", ok=False, error_code="not_found"
            ), None
        try:
            return (
                ToolResult(tool="order_lookup", ok=True, order=to_facts(match)),
                to_gate_order(match),
            )
        except (KeyError, ValueError, TypeError):
            return ToolResult(tool="order_lookup", ok=False, error_code="upstream_error"), None
