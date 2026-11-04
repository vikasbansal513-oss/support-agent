import json
import secrets
import uuid
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.service import Principal
from app.auth.tokens import sha256_hex
from app.contracts.contracts import (
    CourierIn,
    GmailCheckNowOut,
    GupshupIn,
    IntegrationKind,
    IntegrationOut,
    ProbeResultOut,
    ShopifyIn,
)
from app.core import timeutil
from app.core.audit import AuditEvent, record
from app.core.config import get_settings
from app.core.crypto import Crypto
from app.core.db import session_scope
from app.core.errors import AppError
from app.core.masking import secret_hint
from app.integrations.courier import (
    CourierClient,
    DelhiveryClient,
    NotVerifiedCourierClient,
    ShiprocketClient,
)
from app.integrations.gmail import GmailUpstreamError, HttpGmailClient
from app.integrations.http import DbCallRecorder, ExternalHttp
from app.integrations.shopify import ShopifyClient
from app.jobs import queue

# Couriers jinki asli tracking API verify/connect ho chuki hai (apni client class hai).
VERIFIED_COURIER_KINDS: tuple[IntegrationKind, ...] = ("shiprocket", "delhivery")
# Saare couriers jo PUT /integrations/{kind} se configure ho sakte hain (verified + not-yet-verified).
COURIER_KINDS: tuple[IntegrationKind, ...] = (
    "shiprocket",
    "delhivery",
    "bluedart",
    "dtdc",
    "ecom_express",
    "xpressbees",
    "shadowfax",
)
KINDS: tuple[IntegrationKind, ...] = ("shopify", "gupshup", "gmail", *COURIER_KINDS)
Factory = async_sessionmaker[AsyncSession]


def get_transport() -> httpx.AsyncBaseTransport | None:
    return None


def crypto() -> Crypto:
    return Crypto(get_settings().encryption_key)


def _hint(secret: str) -> str:
    return f"Set hai (...{secret_hint(secret)[-4:]})"


def _out(
    kind: IntegrationKind, row: Any | None, hint: str | None, webhook_url: str | None = None
) -> IntegrationOut:
    if row is None:
        return IntegrationOut(
            kind=kind,
            configured=False,
            status="not_configured",
            config={},
            secret_hint=None,
            last_error_code=None,
            last_checked_at=None,
        )
    return IntegrationOut(
        kind=kind,
        configured=True,
        status=row.status,
        config={str(k): str(v) for k, v in (row.config or {}).items()},
        secret_hint=hint,
        last_error_code=row.last_error_code,
        last_checked_at=row.last_checked_at,
        webhook_url=webhook_url,
    )


_SELECT = text(
    "SELECT kind, config, secret_enc, status, last_error_code, last_checked_at "
    "FROM integrations WHERE kind = :k"
)
_SELECT_ALL = text(
    "SELECT kind, config, secret_enc, status, last_error_code, last_checked_at "
    "FROM integrations"
)
_UPSERT = text(
    "INSERT INTO integrations (kind, config, secret_enc, webhook_secret_hash, status,"
    " last_error_code, last_checked_at) VALUES"
    " (:k, CAST(:cfg AS jsonb), :sec, :wh, 'ok', NULL, NULL)"
    " ON CONFLICT (tenant_id, kind) DO UPDATE SET config = EXCLUDED.config,"
    " secret_enc = EXCLUDED.secret_enc,"
    " webhook_secret_hash ="
    " COALESCE(EXCLUDED.webhook_secret_hash, integrations.webhook_secret_hash),"
    " status = 'ok', last_error_code = NULL, last_checked_at = NULL, updated_at = now()"
)


async def _load(session: AsyncSession, kind: IntegrationKind) -> Any | None:
    return (await session.execute(_SELECT, {"k": kind})).first()


def _hint_from_row(row: Any) -> str | None:
    if row is None or not row.secret_enc:
        return None
    data = json.loads(crypto().decrypt(row.secret_enc))
    return _hint(str(data.get("hint_source", "")))


async def list_integrations(session: AsyncSession) -> list[IntegrationOut]:
    # Single combined query instead of one SELECT per kind (was N sequential
    # DB round-trips for len(KINDS) kinds); RLS already scopes the rows to
    # the caller's tenant exactly as the per-kind query did.
    rows = (await session.execute(_SELECT_ALL)).all()
    by_kind = {row.kind: row for row in rows}
    return [_out(kind, by_kind.get(kind), _hint_from_row(by_kind.get(kind))) for kind in KINDS]


async def _audit(session: AsyncSession, principal: Principal, kind: str, action: str) -> None:
    await record(
        session,
        AuditEvent(
            action=action,
            entity_type="integration",
            entity_id=kind,
            actor_user_id=principal.user_id,
            actor_role=principal.role,
        ),
    )


async def store_secret(
    session: AsyncSession,
    kind: IntegrationKind,
    config: dict[str, str],
    secret: dict[str, str],
    hint_source: str,
    webhook_hash: str | None = None,
) -> None:
    payload = json.dumps({**secret, "hint_source": hint_source})
    await session.execute(
        _UPSERT,
        {
            "k": kind,
            "cfg": json.dumps(config),
            "sec": crypto().encrypt(payload),
            "wh": webhook_hash,
        },
    )


async def put_shopify(
    session: AsyncSession, principal: Principal, body: ShopifyIn
) -> IntegrationOut:
    await store_secret(
        session,
        "shopify",
        {"shop_domain": body.shop_domain},
        {"access_token": body.access_token},
        body.access_token,
    )
    await _audit(session, principal, "shopify", "integration.set")
    return _out("shopify", await _load(session, "shopify"), _hint(body.access_token))


async def put_courier(
    session: AsyncSession, principal: Principal, kind: IntegrationKind, body: CourierIn
) -> IntegrationOut:
    if kind == "shiprocket":
        login, sep, password = body.credential.partition(":")
        if not sep or "@" not in login or len(password) < 3:
            raise AppError("E-VAL-001", detail="credential")
        config: dict[str, str] = {"login": login}
        secret = {"email": login, "password": password}
        source = password
    else:
        # delhivery + bluedart/dtdc/ecom_express/xpressbees/shadowfax: single API token/key.
        config = {}
        secret = {"token": body.credential}
        source = body.credential
    await store_secret(session, kind, config, secret, source)
    await _audit(session, principal, kind, "integration.set")
    return _out(kind, await _load(session, kind), _hint(source))


async def put_gupshup(
    session: AsyncSession, principal: Principal, body: GupshupIn, rotate_webhook: bool
) -> IntegrationOut:
    tenant_id = principal.tenant_id
    if tenant_id is None:
        raise AppError("E-AUTH-007")
    owner = (
        await session.execute(
            text("SELECT tenant_id FROM wa_numbers WHERE source_number = :s"),
            {"s": body.source_number},
        )
    ).first()
    if owner is not None and owner.tenant_id != tenant_id:
        raise AppError("E-CONF-001", detail="source_number in use")
    mine = (
        await session.execute(
            text("SELECT source_number FROM wa_numbers WHERE tenant_id = :t"), {"t": tenant_id}
        )
    ).first()
    webhook_url: str | None = None
    webhook_hash: str | None = None
    if mine is None or mine.source_number != body.source_number or rotate_webhook:
        token = secrets.token_urlsafe(32)
        webhook_hash = sha256_hex(token)
        await session.execute(text("DELETE FROM wa_numbers WHERE tenant_id = :t"), {"t": tenant_id})
        await session.execute(
            text(
                "INSERT INTO wa_numbers (source_number, app_name, webhook_token_hash)"
                " VALUES (:s, :a, :h)"
            ),
            {"s": body.source_number, "a": body.app_name, "h": webhook_hash},
        )
        base = get_settings().public_base_url.rstrip("/")
        webhook_url = f"{base}/webhooks/whatsapp/{token}"
    else:
        await session.execute(
            text("UPDATE wa_numbers SET app_name = :a WHERE tenant_id = :t"),
            {"a": body.app_name, "t": tenant_id},
        )
    await store_secret(
        session,
        "gupshup",
        {"source_number": body.source_number, "app_name": body.app_name},
        {"userid": body.userid, "password": body.password},
        body.password,
        webhook_hash,
    )
    await _audit(session, principal, "gupshup", "integration.set")
    return _out("gupshup", await _load(session, "gupshup"), _hint(body.password), webhook_url)


async def load_secret(
    session: AsyncSession, kind: IntegrationKind
) -> tuple[dict[str, str], dict[str, str]] | None:
    row = await _load(session, kind)
    if row is None or not row.secret_enc:
        return None
    data = json.loads(crypto().decrypt(row.secret_enc))
    data.pop("hint_source", None)
    return dict(row.config or {}), {str(k): str(v) for k, v in data.items()}


def build_courier(
    kind: IntegrationKind,
    tenant_id: uuid.UUID,
    config: dict[str, str],
    secret: dict[str, str],
    http: ExternalHttp,
) -> CourierClient:
    if kind == "delhivery":
        return DelhiveryClient(http, secret["token"])
    if kind == "shiprocket":
        return ShiprocketClient(http, tenant_id, secret["email"], secret["password"])
    return NotVerifiedCourierClient(kind)


async def run_probe(
    factory: Factory,
    principal: Principal,
    kind: IntegrationKind,
    transport: httpx.AsyncBaseTransport | None,
) -> ProbeResultOut:
    if principal.tenant_id is None:
        raise AppError("E-AUTH-007")
    return await probe_integration(factory, principal.tenant_id, kind, transport, principal.user_id)


async def probe_integration(
    factory: Factory,
    tenant_id: uuid.UUID,
    kind: IntegrationKind,
    transport: httpx.AsyncBaseTransport | None,
    user_id: uuid.UUID | None = None,
) -> ProbeResultOut:
    async with session_scope(factory, tenant_id=tenant_id, user_id=user_id) as session:
        loaded = await load_secret(session, kind)
        mapped = (
            await session.execute(
                text("SELECT 1 FROM wa_numbers WHERE tenant_id = :t"), {"t": tenant_id}
            )
        ).first()
    if loaded is None:
        raise AppError("E-INT-002")
    config, secret = loaded
    if kind == "gupshup":
        complete = (
            mapped is not None and bool(secret.get("userid")) and bool(config.get("source_number"))
        )
        if not complete:
            return ProbeResultOut(ok=False, detail="config adhura hai", error_code="E-INT-002")
        return ProbeResultOut(
            ok=True, detail="Config save hai. Asli delivery pehle message par verify hogi."
        )
    http = ExternalHttp(kind, recorder=DbCallRecorder(factory, tenant_id), transport=transport)
    auth_error = False
    ok = False
    if kind == "shopify":
        failure = await ShopifyClient(http, config["shop_domain"], secret["access_token"]).probe()
        ok = failure is None
        auth_error = failure is not None and failure.error_code == "auth_error"
    elif kind == "gmail":
        try:
            ok = await HttpGmailClient(http, secret["refresh_token"]).probe()
            auth_error = not ok
        except GmailUpstreamError:
            ok = False
    elif kind not in VERIFIED_COURIER_KINDS:
        # Not yet wired to a real courier API - say so plainly instead of a
        # misleading "auth_error" (credentials weren't even checked).
        return ProbeResultOut(
            ok=False,
            detail="Credential save hai. Is courier ki asli tracking API abhi connect/verify nahi hui.",
            error_code="E-INT-003",
        )
    else:
        client = build_courier(kind, tenant_id, config, secret, http)
        ok = await client.probe()
        auth_error = not ok
    await record_health(factory, tenant_id, kind, ok, auth_error)
    if ok:
        return ProbeResultOut(ok=True)
    return ProbeResultOut(ok=False, detail="connection failed", error_code="E-INT-001")


async def check_now_gmail(factory: Factory, principal: Principal) -> GmailCheckNowOut:
    if principal.tenant_id is None:
        raise AppError("E-AUTH-007")
    tenant_id = principal.tenant_id
    async with session_scope(factory, tenant_id=tenant_id, user_id=principal.user_id) as session:
        loaded = await load_secret(session, "gmail")
        status = (
            await session.execute(text("SELECT status FROM integrations WHERE kind = 'gmail'"))
        ).scalar_one_or_none()
    if loaded is None or status != "ok":
        raise AppError("E-INT-002")
    # Har click par naya job, purana queued/processing job ho bhi to ignore —
    # fetch_gmail khud hi duplicate-safe hai (inbound_events external_id dedup).
    async with session_scope(factory, tenant_id=tenant_id, user_id=principal.user_id) as session:
        job_id = await queue.enqueue(
            session,
            tenant_id,
            "fetch_gmail",
            idempotency_key=f"gmail:manual:{uuid.uuid4().hex}",
            max_attempts=1,
            created_by=principal.user_id,
        )
    # Owner ne khud button dabaya hai — hourly scheduler tick ka wait nahi karwana.
    # Isi request me turant chala do (whatsapp webhook ka wahi drain_now pattern, worker.py
    # ka drain_queue reuse). Fail/race ho jaye to bhi job queue me hi reh jaata hai — agla
    # scheduled tick use normal tareeke se process kar dega, fallback nahi tootta.
    # idempotency_key hamesha fresh uuid4 hai, isliye enqueue() yahan kabhi None nahi
    # deta — guard sirf queue.enqueue() ke general UUID | None return type ke liye (mypy).
    if job_id is None:
        return GmailCheckNowOut(queued=True, job_id=None)
    from app.worker import drain_queue

    done = await drain_queue(factory, job_id, "inline:gmail_check_now")
    # fetch_gmail sirf mail store karta hai aur har mail ke liye alag "process_inbound" job
    # enqueue karta hai (agent ka asli kaam). Unhe bhi yahin turant chalao, warna wo agle
    # hourly tick tak queued pade rehte hain aur dashboard me kuch dikhta nahi. Cap isliye ki
    # HTTP request lamba na ho; bacha hua kaam scheduled tick utha lega.
    async with session_scope(factory, tenant_id=tenant_id, user_id=principal.user_id) as session:
        pending = (
            (
                await session.execute(
                    text(
                        "SELECT id FROM jobs WHERE kind = 'process_inbound' AND status = 'queued'"
                        " ORDER BY created_at LIMIT 10"
                    )
                )
            )
            .scalars()
            .all()
        )
    if pending:
        # API service ka process real agent pipeline by default install nahi karta (sirf
        # worker.py ka main() karta hai). Bina runner ke process_inbound sirf mail ingest
        # karke job "done" kar deta hai, agent reply (Review me dikhne wala) banta hi nahi.
        # Webhooks/whatsapp.py ka _drain_now yahi pattern use karta hai; installed ho to
        # override nahi karte.
        from app.agent import runner as agent_runner
        from app.jobs import handlers as job_handlers

        if not job_handlers.inbound_runner_installed():
            agent_runner.install()
    for pending_id in pending:
        await drain_queue(factory, pending_id, "inline:gmail_check_now")
    return GmailCheckNowOut(queued=True, job_id=job_id, already_running=not done)


GMAIL_RECONNECT_TITLE = "Gmail reconnect karna zaroori hai"
GMAIL_RECONNECT_BODY = (
    "Gmail ka access khatam ho gaya hai, isliye naye emails nahi aayenge. "
    "Integrations me jaakar Gmail dobara connect karein."
)


async def _notify_gmail_reconnect(session: AsyncSession) -> None:
    since = timeutil.utc_now() - timedelta(hours=24)
    exists = (
        await session.execute(
            text("SELECT 1 FROM notifications WHERE title = :t AND created_at >= :s LIMIT 1"),
            {"t": GMAIL_RECONNECT_TITLE, "s": since},
        )
    ).first()
    if exists is not None:
        return
    await session.execute(
        text(
            "INSERT INTO notifications (audience, title, body, link) VALUES ('admin', :t, :b, :l)"
        ),
        {"t": GMAIL_RECONNECT_TITLE, "b": GMAIL_RECONNECT_BODY, "l": "/settings/integrations"},
    )


async def record_health(
    factory: Factory, tenant_id: uuid.UUID, kind: IntegrationKind, ok: bool, auth_error: bool
) -> None:
    now = timeutil.utc_now()
    status = "ok" if ok else ("auth_error" if auth_error else "ok")
    async with session_scope(factory, tenant_id=tenant_id) as session:
        await session.execute(
            text(
                "UPDATE integrations SET status = :s, last_error_code = :e, last_checked_at = :n"
                " WHERE kind = :k"
            ),
            {"s": status, "e": None if ok else "E-INT-001", "n": now, "k": kind},
        )
        if auth_error and kind == "gmail":
            await _notify_gmail_reconnect(session)
        if auth_error and kind in ("shopify", "gupshup"):
            await session.execute(
                text("UPDATE tenants SET agent_paused = true WHERE id = :t"), {"t": tenant_id}
            )
            await record(
                session,
                AuditEvent(
                    action="agent.auto_paused",
                    entity_type="integration",
                    entity_id=kind,
                    reason="integration auth error",
                ),
            )
