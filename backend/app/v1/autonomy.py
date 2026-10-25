import uuid
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.service import Principal
from app.core.audit import AuditEvent, record
from app.core.db import session_scope
from app.core.errors import AppError
from app.policy import service as policy_service
from app.v1.contracts import WRITE_TOOLS
from app.v1.policy import PolicyV2, parse_any

Factory = async_sessionmaker[AsyncSession]
SetLevel = Literal["off", "approval", "auto"]


async def set_level(
    factory: Factory,
    admin: Principal,
    tenant_id: uuid.UUID,
    tool: str,
    level: SetLevel,
    reason: str,
) -> PolicyV2:
    """Admin-only way to put a write tool on `auto`: a new published policy version, audited."""
    if tool not in WRITE_TOOLS:
        raise AppError("E-NF-001")
    actor = Principal(
        user_id=admin.user_id,
        email=admin.email,
        name=admin.name,
        realm="admin",
        role="super_admin",
        tenant_id=tenant_id,
        mfa_ok=True,
    )
    async with session_scope(factory, tenant_id=tenant_id, user_id=admin.user_id) as session:
        mode = (
            await session.execute(text("SELECT mode FROM tenants WHERE id = :t"), {"t": tenant_id})
        ).scalar_one_or_none()
        row = (
            await session.execute(
                text("SELECT content FROM policies WHERE status = 'published' LIMIT 1")
            )
        ).first()
        current = parse_any(row.content) if row else None
        if mode is None or not isinstance(current, PolicyV2):
            raise AppError("E-ADM-003")
        if level == "auto" and mode != "auto":
            raise AppError("E-ADM-003")
        if current.autonomy.level(tool) == level:
            return current
        body = current.model_dump(mode="json")
        body["autonomy"][tool] = level
        if level != "off" and tool not in body["enabled_tools"]:
            body["enabled_tools"] = sorted({*body["enabled_tools"], tool})
        updated = PolicyV2.model_validate(body)
        draft = await policy_service.create_draft(
            session, actor, updated, None, admin_override=True
        )
        await policy_service.publish(session, actor, draft.id, admin_override=True)
        any_auto = any(updated.autonomy.level(t) == "auto" for t in WRITE_TOOLS)
        await session.execute(
            text(
                "UPDATE tenants SET auto_enabled_by = CASE WHEN :a THEN CAST(:u AS uuid) ELSE NULL END,"
                " auto_enabled_at = CASE WHEN :a THEN now() ELSE NULL END WHERE id = :t"
            ),
            {"a": any_auto, "u": admin.user_id, "t": tenant_id},
        )
        await record(
            session,
            AuditEvent(
                action="admin.autonomy_changed",
                entity_type="tenant",
                entity_id=str(tenant_id),
                old_value={"tool": tool, "level": current.autonomy.level(tool)},
                new_value={"tool": tool, "level": level},
                reason=reason,
                actor_user_id=admin.user_id,
                actor_role="super_admin",
            ),
            tenant_id=tenant_id,
        )
        return updated
