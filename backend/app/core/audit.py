import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import is_sensitive_key
from app.core.masking import redact_text


@dataclass(frozen=True)
class AuditEvent:
    action: str
    entity_type: str | None = None
    entity_id: str | None = None
    old_value: Any = None
    new_value: Any = None
    reason: str | None = None
    source_ref: str | None = None
    run_id: str | None = None
    actor_user_id: UUID | None = None
    actor_role: str | None = None


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): "[REDACTED]" if is_sensitive_key(str(k)) else sanitize(v)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [sanitize(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, bool | int | float) or value is None:
        return value
    return redact_text(str(value))


def _dump(value: Any) -> str | None:
    return None if value is None else json.dumps(sanitize(value), ensure_ascii=False)


_INSERT = text(
    "INSERT INTO audit_log (tenant_id, actor_user_id, actor_role, action, entity_type, entity_id,"
    " old_value, new_value, reason, source_ref, run_id) VALUES ("
    "COALESCE(CAST(:tenant_id AS uuid), app_current_tenant()), :actor_user_id, :actor_role,"
    " :action, :entity_type, :entity_id, CAST(:old_value AS jsonb), CAST(:new_value AS jsonb),"
    " :reason, :source_ref, :run_id)"
)


async def record(session: AsyncSession, event: AuditEvent, tenant_id: UUID | None = None) -> None:
    await session.execute(
        _INSERT,
        {
            "tenant_id": str(tenant_id) if tenant_id else None,
            "actor_user_id": event.actor_user_id,
            "actor_role": event.actor_role,
            "action": event.action,
            "entity_type": event.entity_type,
            "entity_id": event.entity_id,
            "old_value": _dump(event.old_value),
            "new_value": _dump(event.new_value),
            "reason": redact_text(event.reason) if event.reason else None,
            "source_ref": event.source_ref,
            "run_id": event.run_id,
        },
    )
