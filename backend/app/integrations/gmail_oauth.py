import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from urllib.parse import urlencode

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditEvent, record
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.errors import AppError
from app.integrations import service
from app.integrations.gmail import API, TOKEN_URL
from app.integrations.http import DbCallRecorder, ExternalHttp, UpstreamError

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
SCOPE_READ = "https://www.googleapis.com/auth/gmail.readonly"
SCOPE_SEND = "https://www.googleapis.com/auth/gmail.send"
STATE_TTL_SECONDS = 600
Factory = async_sessionmaker[AsyncSession]


def redirect_uri() -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/integrations/gmail/callback"


def _state_key() -> bytes:
    secret = get_settings().jwt_secret
    if not secret:
        raise AppError("E-INT-002")
    return hashlib.sha256(f"gmail-oauth-state:{secret}".encode()).digest()


def _sign(body: str) -> str:
    return hmac.new(_state_key(), body.encode(), hashlib.sha256).hexdigest()


def make_state(tenant_id: uuid.UUID, user_id: uuid.UUID) -> str:
    payload = {
        "t": str(tenant_id),
        "u": str(user_id),
        "e": int(time.time()) + STATE_TTL_SECONDS,
        "n": secrets.token_urlsafe(8),
    }
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{body}.{_sign(body)}"


def read_state(state: str) -> tuple[uuid.UUID, uuid.UUID]:
    body, _, signature = state.partition(".")
    if not body or not signature or not hmac.compare_digest(signature, _sign(body)):
        raise AppError("E-VAL-001", detail="state")
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        expires = int(payload["e"])
        tenant_id = uuid.UUID(payload["t"])
        user_id = uuid.UUID(payload["u"])
    except (ValueError, KeyError, TypeError) as exc:
        raise AppError("E-VAL-001", detail="state") from exc
    if expires < int(time.time()):
        raise AppError("E-VAL-001", detail="state expired")
    return tenant_id, user_id


def build_auth_url(tenant_id: uuid.UUID, user_id: uuid.UUID) -> str:
    settings = get_settings()
    if not (
        settings.google_client_id and settings.google_client_secret and settings.public_base_url
    ):
        raise AppError("E-INT-002")
    query = urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": redirect_uri(),
            "response_type": "code",
            "scope": f"{SCOPE_READ} {SCOPE_SEND}",
            "access_type": "offline",
            "prompt": "consent",
            "state": make_state(tenant_id, user_id),
        }
    )
    return f"{AUTH_URL}?{query}"


async def _exchange(http: ExternalHttp, code: str) -> tuple[str, str]:
    settings = get_settings()
    try:
        response = await http.request(
            "POST",
            TOKEN_URL,
            operation="oauth_code",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": redirect_uri(),
            },
        )
    except UpstreamError as exc:
        raise AppError("E-INT-001") from exc
    if response.status_code != 200:
        raise AppError("E-INT-001")
    body = response.json()
    access = body.get("access_token")
    refresh = body.get("refresh_token")
    granted = str(body.get("scope", "")).split()
    if not isinstance(access, str) or not isinstance(refresh, str) or not refresh:
        raise AppError("E-INT-001")
    if SCOPE_READ not in granted or SCOPE_SEND not in granted:
        raise AppError("E-INT-001", detail="scope")
    return access, refresh


async def _address(http: ExternalHttp, access_token: str) -> str:
    try:
        response = await http.request(
            "GET",
            f"{API}/profile",
            operation="profile",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    except UpstreamError as exc:
        raise AppError("E-INT-001") from exc
    address = response.json().get("emailAddress") if response.status_code == 200 else None
    if not isinstance(address, str) or "@" not in address:
        raise AppError("E-INT-001")
    return address.strip().lower()


async def complete_connect(
    factory: Factory,
    code: str,
    state: str,
    transport: httpx.AsyncBaseTransport | None,
) -> str:
    tenant_id, user_id = read_state(state)
    http = ExternalHttp("gmail", recorder=DbCallRecorder(factory, tenant_id), transport=transport)
    access, refresh = await _exchange(http, code)
    address = await _address(http, access)
    async with session_scope(factory, tenant_id=tenant_id, user_id=user_id) as session:
        await service.store_secret(
            session, "gmail", {"address": address}, {"refresh_token": refresh}, refresh
        )
        await record(
            session,
            AuditEvent(
                action="integration.set",
                entity_type="integration",
                entity_id="gmail",
                actor_user_id=user_id,
                actor_role="owner",
            ),
        )
    return address


async def disconnect(
    factory: Factory,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    transport: httpx.AsyncBaseTransport | None,
) -> None:
    async with session_scope(factory, tenant_id=tenant_id, user_id=user_id) as session:
        loaded = await service.load_secret(session, "gmail")
        await session.execute(text("DELETE FROM integrations WHERE kind = 'gmail'"))
        await record(
            session,
            AuditEvent(
                action="integration.removed",
                entity_type="integration",
                entity_id="gmail",
                actor_user_id=user_id,
                actor_role="owner",
            ),
        )
    if loaded is None:
        return
    http = ExternalHttp(
        "gmail", recorder=DbCallRecorder(factory, tenant_id), transport=transport, max_attempts=1
    )
    try:
        await http.request(
            "POST",
            REVOKE_URL,
            operation="oauth_revoke",
            data={"token": loaded[1].get("refresh_token", "")},
        )
    except UpstreamError:
        return
