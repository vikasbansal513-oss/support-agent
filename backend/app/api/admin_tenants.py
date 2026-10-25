import base64
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import require_admin
from app.auth.service import Principal
from app.contracts.contracts import (
    ModeIn,
    Page,
    ReasonIn,
    TenantOut,
    TenantPatchIn,
    TenantStatus,
    UsageLimitIn,
)
from app.core import timeutil
from app.core.audit import AuditEvent, record
from app.core.config import get_settings
from app.core.db import get_session_factory, session_scope
from app.core.errors import AppError

router = APIRouter(prefix="/admin/tenants", tags=["admin-tenants"])
Factory = async_sessionmaker[AsyncSession]

_GET = text(
    "SELECT id, name, slug, status, agent_paused, mode, plan, included_conversations, "
    "usage_override_limit, created_at, approved_at FROM tenants WHERE id = :i"
)
_GET_LOCK = text(
    "SELECT id, name, slug, status, agent_paused, mode, plan, included_conversations, "
    "usage_override_limit, created_at, approved_at FROM tenants WHERE id = :i FOR UPDATE"
)
_LIST = text(
    "SELECT id, name, slug, status, agent_paused, mode, plan, included_conversations, "
    "usage_override_limit, created_at, approved_at FROM tenants "
    "WHERE (CAST(:status AS text) IS NULL OR status = :status) "
    "AND (CAST(:c_at AS timestamptz) IS NULL OR (created_at, id) < (:c_at, CAST(:c_id AS uuid))) "
    "ORDER BY created_at DESC, id DESC LIMIT :limit"
)


def _out(row: Any) -> TenantOut:
    return TenantOut(
        id=row.id,
        name=row.name,
        slug=row.slug,
        status=row.status,
        agent_paused=row.agent_paused,
        mode=row.mode,
        plan=row.plan,
        included_conversations=row.included_conversations,
        usage_override_limit=row.usage_override_limit,
        created_at=row.created_at,
        approved_at=row.approved_at,
    )


def _encode_cursor(created_at: datetime, tenant_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{tenant_id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        stamp, _, ident = raw.partition("|")
        return datetime.fromisoformat(stamp), uuid.UUID(ident)
    except (ValueError, UnicodeDecodeError) as exc:
        raise AppError("E-VAL-001", detail="bad cursor") from exc


async def _get(session: AsyncSession, tenant_id: uuid.UUID, lock: bool = False) -> Any:
    row = (await session.execute(_GET_LOCK if lock else _GET, {"i": tenant_id})).first()
    if row is None:
        raise AppError("E-NF-001")
    return row


@router.get("")
async def list_tenants(
    status: TenantStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> Page:
    params: dict[str, Any] = {"limit": limit + 1, "status": status, "c_at": None, "c_id": None}
    if cursor:
        c_at, c_id = _decode_cursor(cursor)
        params["c_at"], params["c_id"] = c_at, str(c_id)
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        rows = (await session.execute(_LIST, params)).all()
    page = rows[:limit]
    next_cursor = _encode_cursor(page[-1].created_at, page[-1].id) if len(rows) > limit else None
    return Page(items=[_out(r) for r in page], next_cursor=next_cursor)


@router.get("/{tenant_id}")
async def get_tenant(
    tenant_id: uuid.UUID,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> TenantOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        return _out(await _get(session, tenant_id))


@router.patch("/{tenant_id}")
async def patch_tenant(
    tenant_id: uuid.UUID,
    body: TenantPatchIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> TenantOut:
    changes = body.model_dump(exclude_unset=True)
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        before = await _get(session, tenant_id, lock=True)
        if "name" in changes and changes["name"] is not None:
            await session.execute(
                text("UPDATE tenants SET name = :n WHERE id = :i"),
                {"n": changes["name"].strip(), "i": tenant_id},
            )
        if "notes" in changes:
            await session.execute(
                text("UPDATE tenants SET notes = :n WHERE id = :i"),
                {"n": changes["notes"], "i": tenant_id},
            )
        after = await _get(session, tenant_id)
        await record(
            session,
            AuditEvent(
                action="admin.tenant_patched",
                entity_type="tenant",
                entity_id=str(tenant_id),
                old_value={"name": before.name},
                new_value={"name": after.name, "notes_changed": "notes" in changes},
                actor_user_id=admin.user_id,
                actor_role="super_admin",
            ),
            tenant_id=tenant_id,
        )
        return _out(after)


async def _transition(
    factory: Factory,
    admin: Principal,
    tenant_id: uuid.UUID,
    *,
    from_status: str,
    to_status: str,
    action: str,
    reason: str,
    approve: bool = False,
) -> TenantOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        before = await _get(session, tenant_id, lock=True)
        if before.status != from_status:
            raise AppError("E-CONF-001", detail=f"status is {before.status}")
        if approve:
            await session.execute(
                text(
                    "UPDATE tenants SET status = :s, approved_by = :a, approved_at = :now "
                    "WHERE id = :i"
                ),
                {"s": to_status, "a": admin.user_id, "now": timeutil.utc_now(), "i": tenant_id},
            )
        else:
            await session.execute(
                text("UPDATE tenants SET status = :s WHERE id = :i"),
                {"s": to_status, "i": tenant_id},
            )
        await record(
            session,
            AuditEvent(
                action=action,
                entity_type="tenant",
                entity_id=str(tenant_id),
                old_value={"status": from_status},
                new_value={"status": to_status},
                reason=reason,
                actor_user_id=admin.user_id,
                actor_role="super_admin",
            ),
            tenant_id=tenant_id,
        )
        return _out(await _get(session, tenant_id))


@router.post("/{tenant_id}/approve")
async def approve(
    tenant_id: uuid.UUID,
    body: ReasonIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> TenantOut:
    return await _transition(
        factory,
        admin,
        tenant_id,
        from_status="pending_approval",
        to_status="active",
        action="admin.tenant_approved",
        reason=body.reason,
        approve=True,
    )


@router.post("/{tenant_id}/pause")
async def pause(
    tenant_id: uuid.UUID,
    body: ReasonIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> TenantOut:
    return await _transition(
        factory,
        admin,
        tenant_id,
        from_status="active",
        to_status="paused",
        action="admin.tenant_paused",
        reason=body.reason,
    )


@router.post("/{tenant_id}/resume")
async def resume(
    tenant_id: uuid.UUID,
    body: ReasonIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> TenantOut:
    return await _transition(
        factory,
        admin,
        tenant_id,
        from_status="paused",
        to_status="active",
        action="admin.tenant_resumed",
        reason=body.reason,
    )


@router.put("/{tenant_id}/usage-limit")
async def set_usage_limit(
    tenant_id: uuid.UUID,
    body: UsageLimitIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> TenantOut:
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        before = await _get(session, tenant_id, lock=True)
        await session.execute(
            text("UPDATE tenants SET usage_override_limit = :l WHERE id = :i"),
            {"l": body.usage_override_limit, "i": tenant_id},
        )
        await record(
            session,
            AuditEvent(
                action="admin.usage_limit_changed",
                entity_type="tenant",
                entity_id=str(tenant_id),
                old_value={"usage_override_limit": before.usage_override_limit},
                new_value={"usage_override_limit": body.usage_override_limit},
                reason=body.reason,
                actor_user_id=admin.user_id,
                actor_role="super_admin",
            ),
            tenant_id=tenant_id,
        )
        return _out(await _get(session, tenant_id))


@router.put("/{tenant_id}/mode")
async def set_mode(
    tenant_id: uuid.UUID,
    body: ModeIn,
    admin: Principal = Depends(require_admin),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> TenantOut:
    settings = get_settings()
    async with session_scope(factory, super_admin=True, user_id=admin.user_id) as session:
        before = await _get(session, tenant_id, lock=True)
        if body.mode != "shadow" and before.mode == "shadow":
            async with session_scope(factory, tenant_id=tenant_id) as tenant_session:
                counts = (
                    await tenant_session.execute(
                        text(
                            "SELECT count(*) FILTER (WHERE verdict = 'correct') AS correct,"
                            " count(*) AS reviewed FROM shadow_verdicts"
                        )
                    )
                ).one()
            passed = (
                counts.reviewed >= settings.shadow_min_reviewed
                and counts.correct * 100 >= settings.shadow_min_correct_pct * counts.reviewed
            )
            if not passed:
                raise AppError("E-ADM-002")
        await session.execute(
            text("UPDATE tenants SET mode = :m WHERE id = :i"), {"m": body.mode, "i": tenant_id}
        )
        await record(
            session,
            AuditEvent(
                action="admin.mode_changed",
                entity_type="tenant",
                entity_id=str(tenant_id),
                old_value={"mode": before.mode},
                new_value={"mode": body.mode},
                reason=body.reason,
                actor_user_id=admin.user_id,
                actor_role="super_admin",
            ),
            tenant_id=tenant_id,
        )
        return _out(await _get(session, tenant_id))
