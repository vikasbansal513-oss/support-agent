from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import require_owner, require_user
from app.auth.service import Principal
from app.contracts.contracts import NotificationSettings
from app.core.audit import AuditEvent, record
from app.core.db import get_session_factory, session_scope

router = APIRouter(prefix="/settings", tags=["settings"])
Factory = async_sessionmaker[AsyncSession]

_GET = text(
    "SELECT notify_email_enabled, notify_email, notify_whatsapp_enabled, alert_phone,"
    " grievance_email FROM tenant_settings"
)
_PUT = text(
    "INSERT INTO tenant_settings (notify_email_enabled, notify_email, notify_whatsapp_enabled,"
    " alert_phone, grievance_email) VALUES (:ee, :em, :we, :ap, :ge)"
    " ON CONFLICT (tenant_id) DO UPDATE SET notify_email_enabled = EXCLUDED.notify_email_enabled,"
    " notify_email = EXCLUDED.notify_email,"
    " notify_whatsapp_enabled = EXCLUDED.notify_whatsapp_enabled,"
    " alert_phone = EXCLUDED.alert_phone, grievance_email = EXCLUDED.grievance_email,"
    " updated_at = now()"
)


@router.get("/notifications")
async def get_notifications(
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> NotificationSettings:
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as s:
        row = (await s.execute(_GET)).first()
    if row is None:
        return NotificationSettings(notify_email_enabled=True, notify_whatsapp_enabled=False)
    return NotificationSettings(
        notify_email_enabled=row.notify_email_enabled,
        notify_email=row.notify_email,
        notify_whatsapp_enabled=row.notify_whatsapp_enabled,
        alert_phone=row.alert_phone,
        grievance_email=row.grievance_email,
    )


@router.put("/notifications")
async def put_notifications(
    body: NotificationSettings,
    principal: Principal = Depends(require_owner),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> NotificationSettings:
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as s:
        await s.execute(
            _PUT,
            {
                "ee": body.notify_email_enabled,
                "em": body.notify_email,
                "we": body.notify_whatsapp_enabled,
                "ap": body.alert_phone,
                "ge": body.grievance_email,
            },
        )
        await record(
            s,
            AuditEvent(
                action="settings.notifications_updated",
                entity_type="tenant_settings",
                actor_user_id=principal.user_id,
                actor_role=principal.role,
            ),
        )
    return body
