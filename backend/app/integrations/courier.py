import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from app.contracts.contracts import ToolResult, TrackingFacts
from app.integrations.http import ExternalHttp, UpstreamError

_token_cache: dict[tuple[uuid.UUID, str], str] = {}


def clear_token_cache() -> None:
    _token_cache.clear()


class CourierClient(Protocol):
    async def track(self, awb: str) -> ToolResult: ...

    async def probe(self) -> bool: ...


def _fail(code: str) -> ToolResult:
    return ToolResult(tool="tracking_lookup", ok=False, error_code=code)


def _dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class DelhiveryClient:
    BASE = "https://track.delhivery.com/api/v1/packages/json/"

    def __init__(self, http: ExternalHttp, token: str) -> None:
        self._http = http
        self._token = token

    async def probe(self) -> bool:
        result = await self.track("0000000000000")
        return result.error_code not in ("auth_error", "upstream_error")

    async def track(self, awb: str) -> ToolResult:
        try:
            response = await self._http.request(
                "GET",
                self.BASE,
                operation="track",
                headers={"Authorization": f"Token {self._token}", "Accept": "application/json"},
                params={"waybill": awb},
            )
        except UpstreamError:
            return _fail("upstream_error")
        if response.status_code in (401, 403):
            return _fail("auth_error")
        if response.status_code != 200:
            return _fail("upstream_error")
        try:
            shipments = response.json().get("ShipmentData") or []
            if not shipments:
                return _fail("not_found")
            shipment = shipments[0]["Shipment"]
            status = shipment["Status"]
            return ToolResult(
                tool="tracking_lookup",
                ok=True,
                tracking=TrackingFacts(
                    awb=awb[:60],
                    courier="Delhivery",
                    status_text=str(status.get("Status") or "Unknown")[:120],
                    last_update_at=_dt(status.get("StatusDateTime")),
                    expected_delivery=_dt(shipment.get("ExpectedDeliveryDate")),
                ),
            )
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            return _fail("upstream_error")


class ShiprocketClient:
    BASE = "https://apiv2.shiprocket.in/v1/external"

    def __init__(self, http: ExternalHttp, tenant_id: uuid.UUID, email: str, password: str) -> None:
        self._http = http
        self._tenant_id = tenant_id
        self._email = email
        self._password = password

    async def _login(self) -> str | None:
        try:
            response = await self._http.request(
                "POST",
                f"{self.BASE}/auth/login",
                operation="login",
                json={"email": self._email, "password": self._password},
            )
        except UpstreamError:
            return None
        if response.status_code != 200:
            return None
        token = response.json().get("token")
        if not isinstance(token, str) or not token:
            return None
        _token_cache[(self._tenant_id, "shiprocket")] = token
        return token

    async def probe(self) -> bool:
        return await self._login() is not None

    async def track(self, awb: str) -> ToolResult:
        key = (self._tenant_id, "shiprocket")
        token = _token_cache.get(key) or await self._login()
        if token is None:
            return _fail("auth_error")
        for attempt in range(2):
            try:
                response = await self._http.request(
                    "GET",
                    f"{self.BASE}/courier/track/awb/{awb}",
                    operation="track",
                    headers={"Authorization": f"Bearer {token}"},
                )
            except UpstreamError:
                return _fail("upstream_error")
            if response.status_code in (401, 403):
                _token_cache.pop(key, None)
                if attempt == 0:
                    fresh = await self._login()
                    if fresh is None:
                        return _fail("auth_error")
                    token = fresh
                    continue
                return _fail("auth_error")
            break
        if response.status_code == 404:
            return _fail("not_found")
        if response.status_code != 200:
            return _fail("upstream_error")
        try:
            data = response.json()["tracking_data"]
            if data.get("track_status") == 0 or not data.get("shipment_track"):
                return _fail("not_found")
            track = data["shipment_track"][0]
            return ToolResult(
                tool="tracking_lookup",
                ok=True,
                tracking=TrackingFacts(
                    awb=awb[:60],
                    courier=str(track.get("courier_name") or "Shiprocket")[:60],
                    status_text=str(track.get("current_status") or "Unknown")[:120],
                    last_update_at=_dt(track.get("updated_time_stamp")),
                    expected_delivery=_dt(data.get("etd")),
                ),
            )
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            return _fail("upstream_error")
