import base64
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from app.contracts.contracts import OrderFacts, OrderLine, ToolResult
from app.integrations.http import ExternalHttp, UpstreamError
from app.integrations.shopify import _customer_contact, _digits, _parse_dt, phones_match
from app.v1.gate import GateOrder

API_PREFIX = "wp-json/wc/v3"
TRACKING_META_KEY = "_wc_shipment_tracking_items"
CANCELLED_STATUSES = frozenset({"cancelled", "failed", "trash"})
PENDING_STATUSES = frozenset({"pending", "on-hold", "auto-draft", "checkout-draft"})


def paise(value: Any) -> int:
    """WooCommerce sends money as strings ("1499.00") but line prices as JSON numbers; going
    through Decimal(str(...)) keeps float noise out. Never float arithmetic."""
    try:
        amount = Decimal(str(value if value not in (None, "") else "0"))
    except InvalidOperation:
        return 0
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def order_id_of(order_ref: str) -> str:
    return _digits(order_ref)


def order_name(order: dict[str, Any]) -> str:
    return f"#{order.get('number') or order.get('id')}"[:40]


def meta_value(order: dict[str, Any], key: str) -> Any:
    for item in order.get("meta_data") or []:
        if isinstance(item, dict) and item.get("key") == key:
            return item.get("value")
    return None


def meta_keys(order: dict[str, Any]) -> set[str]:
    return {
        str(item.get("key"))
        for item in order.get("meta_data") or []
        if isinstance(item, dict) and item.get("key") is not None
    }


def refunded_paise(order: dict[str, Any]) -> int:
    return sum(abs(paise(refund.get("total"))) for refund in order.get("refunds") or [])


def customer_matches(order: dict[str, Any], phone_e164: str | None, email: str | None) -> bool:
    if not phone_e164 and not email:
        return False
    billing = order.get("billing") or {}
    shipping = order.get("shipping") or {}
    if phone_e164:
        for candidate in (billing.get("phone"), shipping.get("phone")):
            if phones_match(phone_e164, candidate):
                return True
    if email:
        wanted = email.strip().lower()
        candidate = billing.get("email")
        if isinstance(candidate, str) and candidate.strip().lower() == wanted:
            return True
    return False


def _tracking_items(order: dict[str, Any]) -> list[dict[str, Any]]:
    value = meta_value(order, TRACKING_META_KEY)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _customer_name(order: dict[str, Any]) -> str | None:
    for source in (order.get("billing") or {}, order.get("shipping") or {}):
        first = str(source.get("first_name") or "").strip()
        last = str(source.get("last_name") or "").strip()
        full = f"{first} {last}".strip()
        if full:
            return full[:200]
    return None


def financial_status(order: dict[str, Any]) -> str:
    status = str(order.get("status") or "pending")
    total = paise(order.get("total"))
    refunded = refunded_paise(order)
    if status == "refunded" or (total > 0 and refunded >= total):
        return "refunded"
    if status in CANCELLED_STATUSES:
        return "voided"
    if refunded > 0:
        return "partially_refunded"
    unpaid = not order.get("date_paid_gmt") and not order.get("date_paid")
    if status in PENDING_STATUSES or unpaid:
        return "pending"
    return "paid"


def fulfillment_status(order: dict[str, Any]) -> str:
    """WooCommerce has no fulfillment object. 'completed' is the merchant's shipped/done mark;
    a shipment-tracking entry also counts as shipped, so cancel / address change stay blocked
    once a parcel has a tracking number."""
    if str(order.get("status")) == "completed" or _tracking_items(order):
        return "fulfilled"
    return "unfulfilled"


def to_facts(order: dict[str, Any]) -> OrderFacts:
    contact_phone, contact_email = _customer_contact(
        {
            "phone": (order.get("billing") or {}).get("phone"),
            "email": (order.get("billing") or {}).get("email"),
            "shipping_address": {"phone": (order.get("shipping") or {}).get("phone")},
        }
    )
    numbers: list[str] = []
    courier: str | None = None
    for item in _tracking_items(order):
        number = item.get("tracking_number")
        if number and str(number) not in numbers:
            numbers.append(str(number))
        provider = item.get("custom_tracking_provider") or item.get("tracking_provider")
        if courier is None and provider:
            courier = str(provider)
    created = order.get("date_created_gmt") or order.get("date_created")
    return OrderFacts(
        order_name=order_name(order),
        financial_status=financial_status(order)[:40],
        fulfillment_status=fulfillment_status(order),
        lines=[
            OrderLine(
                title=str(line.get("name") or "item")[:200],
                quantity=max(1, min(int(line.get("quantity") or 1), 10000)),
                unit_price_paise=paise(line.get("price")),
            )
            for line in (order.get("line_items") or [])[:50]
        ],
        total_paise=paise(order.get("total")),
        created_at=_parse_dt(str(created)),
        tracking_numbers=numbers[:10],
        courier=courier,
        customer_name=_customer_name(order),
        customer_phone=contact_phone,
        customer_email=contact_email,
    )


def _delivered_at(order: dict[str, Any]) -> datetime | None:
    """VERIFY: WooCommerce has no delivered date. The date the merchant marked the order
    'completed' is used, and only for completed orders; anything else stays unknown so the
    gate sends it to a human or to Approvals."""
    if str(order.get("status")) != "completed":
        return None
    completed = order.get("date_completed_gmt") or order.get("date_completed")
    return _parse_dt(str(completed)) if completed else None


def to_gate_order(order: dict[str, Any]) -> GateOrder:
    """VERIFY: refunds, status mapping and the completed-date delivery proxy are assumed from the
    WooCommerce REST v3 order shape; not checked against a live store. WooCommerce orders carry no
    tags, so tag-based return windows, final-sale tags and dispute tags never match here."""
    return GateOrder(
        order_ref=order_name(order),
        total_paise=paise(order.get("total")),
        fulfillment_status=fulfillment_status(order),
        financial_status=financial_status(order)[:40],
        cancelled=str(order.get("status")) in CANCELLED_STATUSES,
        refunded_paise=refunded_paise(order),
        delivered_at=_delivered_at(order),
        tags=(),
        disputed=False,
    )


def basic_auth_header(consumer_key: str, consumer_secret: str) -> str:
    token = base64.b64encode(f"{consumer_key}:{consumer_secret}".encode()).decode()
    return f"Basic {token}"


class WooCommerceClient:
    def __init__(
        self, http: ExternalHttp, store_url: str, consumer_key: str, consumer_secret: str
    ) -> None:
        self._http = http
        self._base = store_url.rstrip("/")
        self._auth = basic_auth_header(consumer_key, consumer_secret)

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": self._auth, "Accept": "application/json"}

    def _url(self, path: str) -> str:
        return f"{self._base}/{API_PREFIX}/{path}"

    async def probe(self) -> ToolResult | None:
        try:
            response = await self._http.request(
                "GET",
                self._url("orders"),
                operation="shop",
                headers=self._headers,
                params={"per_page": "1"},
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
        number = order_id_of(order_ref)
        if not number:
            return ToolResult(tool="order_lookup", ok=False, error_code="not_found"), None
        try:
            response = await self._http.request(
                "GET",
                self._url(f"orders/{number}"),
                operation="order_lookup",
                headers=self._headers,
            )
            match: dict[str, Any] | None = None
            if response.status_code == 200:
                payload = response.json()
                match = payload if isinstance(payload, dict) else None
            elif response.status_code == 404:
                # Stores with a sequential-order-number plugin: the public number is not the
                # post id, so fall back to a search and match the number exactly.
                response = await self._http.request(
                    "GET",
                    self._url("orders"),
                    operation="order_lookup",
                    headers=self._headers,
                    params={"search": number, "per_page": "5"},
                )
                if response.status_code == 200:
                    payload = response.json()
                    rows = payload if isinstance(payload, list) else []
                    match = next(
                        (o for o in rows if isinstance(o, dict) and str(o.get("number")) == number),
                        None,
                    )
        except UpstreamError:
            return ToolResult(tool="order_lookup", ok=False, error_code="upstream_error"), None
        except ValueError:
            return ToolResult(tool="order_lookup", ok=False, error_code="upstream_error"), None
        if response.status_code in (401, 403):
            return ToolResult(tool="order_lookup", ok=False, error_code="auth_error"), None
        if response.status_code not in (200, 404):
            return ToolResult(tool="order_lookup", ok=False, error_code="upstream_error"), None
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
