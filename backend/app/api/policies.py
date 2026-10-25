import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import require_owner, require_user
from app.auth.service import Principal
from app.contracts.contracts import RegressionOut
from app.core.db import get_session_factory, session_scope
from app.core.errors import AppError
from app.policy import service
from app.v1.policy import PolicyDraftAnyIn, PolicyOutAny

router = APIRouter(prefix="/policies", tags=["policies"])
Factory = async_sessionmaker[AsyncSession]


def _parse(body: dict[str, Any]) -> PolicyDraftAnyIn:
    try:
        return PolicyDraftAnyIn.model_validate(body)
    except ValidationError as exc:
        fields = ",".join(".".join(str(p) for p in e["loc"]) for e in exc.errors())
        raise AppError("E-POL-002", detail=fields) from exc


@router.get("")
async def list_policies(
    principal: Principal = Depends(require_user),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> list[PolicyOutAny]:
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as s:
        return await service.list_policies(s)


@router.post("", status_code=201)
async def create_policy(
    body: dict[str, Any],
    principal: Principal = Depends(require_owner),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> PolicyOutAny:
    parsed = _parse(body)
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as s:
        return await service.create_draft(s, principal, parsed.content, parsed.base_version)


@router.put("/{policy_id}")
async def update_policy(
    policy_id: uuid.UUID,
    body: dict[str, Any],
    principal: Principal = Depends(require_owner),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> PolicyOutAny:
    parsed = _parse(body)
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as s:
        return await service.update_draft(
            s, principal, policy_id, parsed.content, parsed.base_version
        )


@router.get("/{policy_id}/regression")
async def policy_regression(
    policy_id: uuid.UUID,
    principal: Principal = Depends(require_owner),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> RegressionOut:
    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as s:
        return await service.regression(s, principal, policy_id)


@router.post("/{policy_id}/publish")
async def publish_policy(
    policy_id: uuid.UUID,
    principal: Principal = Depends(require_owner),  # noqa: B008
    factory: Factory = Depends(get_session_factory),  # noqa: B008
) -> PolicyOutAny:
    try:
        async with session_scope(
            factory, tenant_id=principal.tenant_id, user_id=principal.user_id
        ) as s:
            return await service.publish(s, principal, policy_id)
    except AppError as exc:
        if exc.code == "E-POL-001":
            await _persist_block(factory, principal, policy_id, exc)
        raise


async def _persist_block(
    factory: Factory, principal: Principal, policy_id: uuid.UUID, exc: AppError
) -> None:
    from app.core.audit import AuditEvent, record

    async with session_scope(
        factory, tenant_id=principal.tenant_id, user_id=principal.user_id
    ) as s:
        await record(
            s,
            AuditEvent(
                action="policy.publish_blocked",
                entity_type="policy",
                entity_id=str(policy_id),
                reason=exc.detail,
                actor_user_id=principal.user_id,
                actor_role=principal.role,
            ),
        )
