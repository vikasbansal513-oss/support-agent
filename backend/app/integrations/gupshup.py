import uuid
from dataclasses import dataclass, field
from typing import Protocol

from app.integrations.http import ExternalHttp, UpstreamError

SEND_URL = "https://media.smsgupshup.com/GatewayAPI/rest"


class SendError(Exception):
    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class GupshupSender(Protocol):
    async def send_text(self, to_e164: str, text: str) -> str: ...


class HttpGupshupSender:
    """Gupshup enterprise GatewayAPI SendMessage. VERIFY request/response shape with a real account."""

    def __init__(self, http: ExternalHttp, userid: str, password: str) -> None:
        self._http = http
        self._userid = userid
        self._password = password

    async def send_text(self, to_e164: str, text: str) -> str:
        try:
            response = await self._http.request(
                "POST",
                SEND_URL,
                operation="send_text",
                data={
                    "method": "SendMessage",
                    "send_to": to_e164.lstrip("+"),
                    "msg": text,
                    "msg_type": "TEXT",
                    "userid": self._userid,
                    "password": self._password,
                    "auth_scheme": "plain",
                    "v": "1.1",
                    "format": "json",
                },
            )
        except UpstreamError as exc:
            raise SendError(exc.kind) from exc
        if response.status_code in (401, 403):
            raise SendError("auth_error")
        if response.status_code != 200:
            raise SendError(f"http_{response.status_code}")
        try:
            body = response.json().get("response", {})
            if str(body.get("status", "")).lower() != "success":
                raise SendError("rejected")
            return str(body.get("id") or uuid.uuid4())
        except (ValueError, AttributeError) as exc:
            raise SendError("bad_body") from exc


@dataclass
class FakeGupshupSender:
    sent: list[tuple[str, str]] = field(default_factory=list)
    fail_with: str | None = None

    async def send_text(self, to_e164: str, text: str) -> str:
        if self.fail_with:
            raise SendError(self.fail_with)
        self.sent.append((to_e164, text))
        return f"fake-{len(self.sent)}"
