import base64
from email.message import EmailMessage
from typing import Any, Protocol

from app.core.config import get_settings
from app.integrations.http import ExternalHttp, UpstreamError

TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105
API = "https://gmail.googleapis.com/gmail/v1/users/me"


class GmailAuthError(Exception):
    pass


class GmailUpstreamError(Exception):
    pass


class GmailClient(Protocol):
    async def list_message_ids(self, query: str, limit: int) -> list[str]: ...

    async def get_message(self, message_id: str) -> dict[str, Any]: ...

    async def probe(self) -> bool: ...


class EmailSender(Protocol):
    async def send_reply(
        self,
        *,
        sender: str,
        to: str,
        subject: str,
        body: str,
        thread_id: str | None,
        in_reply_to: str | None,
    ) -> str: ...


class HttpGmailClient:
    def __init__(self, http: ExternalHttp, refresh_token: str) -> None:
        self._http = http
        self._refresh_token = refresh_token
        self._access_token: str | None = None

    async def _token(self) -> str:
        if self._access_token:
            return self._access_token
        settings = get_settings()
        try:
            response = await self._http.request(
                "POST",
                TOKEN_URL,
                operation="oauth_refresh",
                data={
                    "grant_type": "refresh_token",
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "refresh_token": self._refresh_token,
                },
            )
        except UpstreamError as exc:
            raise GmailUpstreamError(exc.kind) from exc
        if response.status_code in (400, 401):
            raise GmailAuthError("refresh rejected")
        if response.status_code != 200:
            raise GmailUpstreamError(f"http_{response.status_code}")
        token = response.json().get("access_token")
        if not isinstance(token, str) or not token:
            raise GmailUpstreamError("no_access_token")
        self._access_token = token
        return token

    async def _get(self, path: str, operation: str, params: dict[str, str]) -> dict[str, Any]:
        token = await self._token()
        try:
            response = await self._http.request(
                "GET",
                f"{API}{path}",
                operation=operation,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
        except UpstreamError as exc:
            raise GmailUpstreamError(exc.kind) from exc
        if response.status_code in (401, 403):
            raise GmailAuthError("access rejected")
        if response.status_code != 200:
            raise GmailUpstreamError(f"http_{response.status_code}")
        data = response.json()
        if not isinstance(data, dict):
            raise GmailUpstreamError("bad_body")
        return data

    async def probe(self) -> bool:
        try:
            await self._get("/profile", "profile", {})
        except GmailAuthError:
            return False
        return True

    async def list_message_ids(self, query: str, limit: int) -> list[str]:
        data = await self._get("/messages", "list", {"q": query, "maxResults": str(limit)})
        return [
            str(m["id"]) for m in data.get("messages") or [] if isinstance(m, dict) and "id" in m
        ]

    async def get_message(self, message_id: str) -> dict[str, Any]:
        return await self._get(f"/messages/{message_id}", "get", {"format": "full"})

    async def send_reply(
        self,
        *,
        sender: str,
        to: str,
        subject: str,
        body: str,
        thread_id: str | None,
        in_reply_to: str | None,
    ) -> str:
        message = EmailMessage()
        message["From"] = sender
        message["To"] = to
        message["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
            message["References"] = in_reply_to
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        payload: dict[str, Any] = {"raw": raw}
        if thread_id:
            payload["threadId"] = thread_id
        token = await self._token()
        try:
            response = await self._http.request(
                "POST",
                f"{API}/messages/send",
                operation="send",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
        except UpstreamError as exc:
            raise GmailUpstreamError(exc.kind) from exc
        if response.status_code in (401, 403):
            raise GmailAuthError("send rejected")
        if response.status_code != 200:
            raise GmailUpstreamError(f"http_{response.status_code}")
        sent_id = response.json().get("id")
        if not isinstance(sent_id, str):
            raise GmailUpstreamError("bad_body")
        return sent_id
