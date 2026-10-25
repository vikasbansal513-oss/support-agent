import time
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import require_admin
from app.auth.service import Principal
from app.core.audit import AuditEvent, record
from app.core.db import get_session_factory, session_scope
from app.core.errors import AppError
from app.integrations.http import ExternalHttp
from app.llm.provider import ProviderError, ProviderTimeout, make_provider
from app.llm.runtime import EffectiveLlm, encrypt_key, from_env, load_effective, validate_choice

router = APIRouter(prefix="/admin/llm-settings", tags=["admin-llm-settings"])
Factory = async_sessionmaker[AsyncSession]
ProviderName = Literal["anthropic", "openai", "groq", "openrouter", "openai_compatible"]


class LlmSettingsOut(BaseModel):
    provider: ProviderName
    model: str
    base_url: str
    has_key: bool
    source: Literal["dashboard", "deploy"]
    updated_at: datetime | None = None


class LlmChoiceIn(BaseModel):
    provider: ProviderName
    model: str = Field(min_length=1, max_length=200)
    base_url: str = Field(default="", max_length=300)
    api_key: str | None = Field(default=None, min_length=8, max_length=500)


class LlmSettingsIn(LlmChoiceIn):
    reason: str = Field(min_length=5, max_length=300)


class LlmTestOut(BaseModel):
    ok: bool
    latency_ms: int
    error: str | None = None


def _out(current: EffectiveLlm, updated_at: datetime | None) -> LlmSettingsOut:
    return LlmSettingsOut(
        provider=current.provider,
        model=current.model,
        base_url=current.base_url,
        has_key=bool(current.api_key),
        source=current.source,
        updated_at=updated_at,
    )


async def _stored(session: AsyncSession) -> tuple[str, str | None, datetime] | None:
    row = (
        await session.execute(
            text("SELECT provider, api_key_enc, updated_at FROM platform_llm_settings WHERE id = 1")
        )
    ).first()
    return (row.provider, row.api_key_enc, row.updated_at) if row else None


def _check(body: LlmChoiceIn) -> None:
    problem = validate_choice(body.provider, body.model, body.base_url)
    if problem:
        raise AppError("E-VAL-001", detail=problem)


@router.get("")
async def read_settings(
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> LlmSettingsOut:
    current = await load_effective(factory)
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        stored = await _stored(session)
    return _out(current, stored[2] if stored else None)


@router.put("")
async def save_settings(
    body: LlmSettingsIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> LlmSettingsOut:
    _check(body)
    env = from_env()
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        stored = await _stored(session)
        key_enc: str | None
        if body.api_key:
            key_enc = encrypt_key(body.api_key)
        elif stored and stored[0] == body.provider and stored[1]:
            key_enc = stored[1]
        elif body.provider == env.provider and env.api_key:
            key_enc = None
        else:
            raise AppError("E-VAL-001", detail="api_key is required for this provider")
        row = (
            await session.execute(
                text(
                    "INSERT INTO platform_llm_settings"
                    " (id, provider, model, base_url, api_key_enc, updated_by, updated_at)"
                    " VALUES (1, :p, :m, :b, :k, :u, now())"
                    " ON CONFLICT (id) DO UPDATE SET provider = :p, model = :m, base_url = :b,"
                    " api_key_enc = :k, updated_by = :u, updated_at = now()"
                    " RETURNING updated_at"
                ),
                {
                    "p": body.provider,
                    "m": body.model.strip(),
                    "b": body.base_url.strip(),
                    "k": key_enc,
                    "u": admin.user_id,
                },
            )
        ).one()
        await record(
            session,
            AuditEvent(
                action="admin.llm_settings_changed",
                entity_type="platform",
                entity_id="llm_settings",
                old_value={"provider": stored[0]} if stored else {"provider": env.provider},
                new_value={
                    "provider": body.provider,
                    "model": body.model.strip(),
                    "base_url": body.base_url.strip(),
                    "key_updated": bool(body.api_key),
                },
                reason=body.reason,
                actor_user_id=admin.user_id,
                actor_role="admin",
            ),
        )
    current = await load_effective(factory)
    return _out(current, row.updated_at)


@router.post("/test")
async def test_settings(
    body: LlmChoiceIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> LlmTestOut:
    _check(body)
    current = await load_effective(factory)
    api_key = body.api_key or (current.api_key if current.provider == body.provider else "")
    if not api_key:
        raise AppError("E-VAL-001", detail="api_key is required for this provider")
    http = ExternalHttp(body.provider, timeout=15.0, max_attempts=1)
    provider = make_provider(http, body.provider, body.model.strip(), api_key, body.base_url)
    started = time.monotonic()
    error: str | None = None
    try:
        await provider.complete("Reply with the single word: ok", "ok", 16)
    except ProviderTimeout:
        error = "timeout"
    except ProviderError as exc:
        error = exc.kind
    return LlmTestOut(
        ok=error is None, latency_ms=int((time.monotonic() - started) * 1000), error=error
    )
