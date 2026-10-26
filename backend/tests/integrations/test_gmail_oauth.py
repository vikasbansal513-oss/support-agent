from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.integrations.service import get_transport
from tests.auth.helpers import MemoryMailer, active_user, bearer, login

Factory = async_sessionmaker[AsyncSession]
SCOPES = "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send"


class Google:
    def __init__(self) -> None:
        self.scope = SCOPES
        self.refresh: str | None = "1//refresh-token-abcdef"
        self.revoked: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/token":
            body: dict[str, str] = {"access_token": "ya29.access", "scope": self.scope}
            if self.refresh:
                body["refresh_token"] = self.refresh
            return httpx.Response(200, json=body)
        if path.endswith("/profile"):
            return httpx.Response(200, json={"emailAddress": "Support@Brand.com"})
        if path == "/revoke":
            self.revoked.append(request.content.decode())
            return httpx.Response(200, json={})
        return httpx.Response(404)


@pytest.fixture
def google() -> Google:
    return Google()


@pytest.fixture
async def api(
    client: httpx.AsyncClient, google: Google, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://api.example.com/")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret-value")
    get_settings.cache_clear()
    app = client._transport.app  # type: ignore[attr-defined]
    app.dependency_overrides[get_transport] = lambda: httpx.MockTransport(google.handler)
    yield client
    get_settings.cache_clear()


async def start(
    api: httpx.AsyncClient, mailer: MemoryMailer, factory: Factory
) -> tuple[dict[str, str], str]:
    email = await active_user(api, mailer, factory)
    headers = bearer(await login(api, email))
    out = await api.post("/integrations/gmail/connect", headers=headers)
    assert out.status_code == 200, out.text
    url = urlsplit(out.json()["url"])
    query = parse_qs(url.query)
    assert url.netloc == "accounts.google.com"
    assert query["redirect_uri"] == ["https://api.example.com/integrations/gmail/callback"]
    assert query["access_type"] == ["offline"] and query["prompt"] == ["consent"]
    assert "gmail.send" in query["scope"][0] and "gmail.readonly" in query["scope"][0]
    return headers, query["state"][0]


async def test_connect_then_status_then_disconnect(
    api: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory, google: Google
) -> None:
    headers, state = await start(api, mailer, runtime_factory)
    back = await api.get("/integrations/gmail/callback", params={"code": "c1", "state": state})
    assert back.status_code == 303
    assert (
        back.headers["location"] == "https://app.example.test/settings/integrations?gmail=connected"
    )
    listed = {i["kind"]: i for i in (await api.get("/integrations", headers=headers)).json()}
    assert listed["gmail"]["configured"] and listed["gmail"]["status"] == "ok"
    assert listed["gmail"]["config"] == {"address": "support@brand.com"}
    assert "refresh-token" not in str(listed)
    gone = await api.delete("/integrations/gmail", headers=headers)
    assert gone.status_code == 200 and gone.json()["configured"] is False
    assert "refresh-token-abcdef" in google.revoked[0]
    listed = {i["kind"]: i for i in (await api.get("/integrations", headers=headers)).json()}
    assert not listed["gmail"]["configured"]
    assert (await api.delete("/integrations/gmail", headers=headers)).status_code == 200


async def test_callback_rejects_bad_state_and_denied(
    api: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory
) -> None:
    headers, state = await start(api, mailer, runtime_factory)
    for params in (
        {"code": "c1", "state": state + "x"},
        {"code": "c1", "state": "abc.def"},
        {"error": "access_denied", "state": state},
        {"state": state},
    ):
        r = await api.get("/integrations/gmail/callback", params=params)
        assert r.status_code == 303 and r.headers["location"].endswith("gmail=error")
    listed = {i["kind"]: i for i in (await api.get("/integrations", headers=headers)).json()}
    assert not listed["gmail"]["configured"]


async def test_callback_needs_full_scope_and_refresh_token(
    api: httpx.AsyncClient, mailer: MemoryMailer, runtime_factory: Factory, google: Google
) -> None:
    headers, state = await start(api, mailer, runtime_factory)
    google.scope = "https://www.googleapis.com/auth/gmail.readonly"
    r = await api.get("/integrations/gmail/callback", params={"code": "c1", "state": state})
    assert r.headers["location"].endswith("gmail=error")
    google.scope = SCOPES
    google.refresh = None
    r = await api.get("/integrations/gmail/callback", params={"code": "c1", "state": state})
    assert r.headers["location"].endswith("gmail=error")
    listed = {i["kind"]: i for i in (await api.get("/integrations", headers=headers)).json()}
    assert not listed["gmail"]["configured"]


async def test_connect_needs_auth_and_google_config(
    api: httpx.AsyncClient,
    mailer: MemoryMailer,
    runtime_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert (await api.post("/integrations/gmail/connect")).status_code == 401
    assert (await api.delete("/integrations/gmail")).status_code == 401
    email = await active_user(api, mailer, runtime_factory)
    headers = bearer(await login(api, email))
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    get_settings.cache_clear()
    r = await api.post("/integrations/gmail/connect", headers=headers)
    assert r.status_code == 400 and r.json()["code"] == "E-INT-002"
