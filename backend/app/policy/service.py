import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import Principal
from app.contracts.contracts import RegressionOut
from app.core import timeutil
from app.core.audit import AuditEvent, record
from app.core.errors import AppError
from app.policy.regression import run_regression
from app.v1.policy import (
    AnyPolicy,
    PolicyOutAny,
    PolicyV2,
    limits_raised_under_auto,
    parse_any,
    raised_levels,
)

_COLS = "id, version, status, content, content_hash, created_at, published_at, regression_run_id"


def content_hash(policy: AnyPolicy) -> str:
    canonical = json.dumps(policy.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _out(row: Any) -> PolicyOutAny:
    return PolicyOutAny(
        id=row.id,
        version=row.version,
        status=row.status,
        content=parse_any(row.content),
        created_at=row.created_at,
        published_at=row.published_at,
        regression_run_id=row.regression_run_id,
    )


async def _guard_content(session: AsyncSession, content: AnyPolicy) -> None:
    row = (
        await session.execute(
            text("SELECT content FROM policies WHERE status = 'published' LIMIT 1")
        )
    ).first()
    published = parse_any(row.content) if row else None
    if isinstance(published, PolicyV2) and not isinstance(content, PolicyV2):
        raise AppError("E-POL-002", detail="schema_downgrade")
    if isinstance(content, PolicyV2):
        base = published if isinstance(published, PolicyV2) else None
        raised = raised_levels(content, base)
        if raised:
            raise AppError("E-POL-002", detail="auto_needs_admin:" + ",".join(raised))
        if base is not None:
            risen = limits_raised_under_auto(content, base)
            if risen:
                raise AppError("E-POL-002", detail="limit_raise_needs_admin:" + ",".join(risen))


async def list_policies(session: AsyncSession) -> list[PolicyOutAny]:
    rows = (
        await session.execute(text(f"SELECT {_COLS} FROM policies ORDER BY version DESC"))
    ).all()
    return [_out(r) for r in rows]


async def _get(session: AsyncSession, policy_id: uuid.UUID, lock: bool = False) -> Any:
    suffix = " FOR UPDATE" if lock else ""
    row = (
        await session.execute(
            text(f"SELECT {_COLS} FROM policies WHERE id = :i{suffix}"), {"i": policy_id}
        )
    ).first()
    if row is None:
        raise AppError("E-NF-001")
    return row


async def create_draft(
    session: AsyncSession,
    principal: Principal,
    content: AnyPolicy,
    base_version: int | None,
    *,
    admin_override: bool = False,
) -> PolicyOutAny:
    await session.execute(text("SELECT id FROM tenants WHERE id = app_current_tenant() FOR UPDATE"))
    if not admin_override:
        await _guard_content(session, content)
    latest = (await session.execute(text("SELECT max(version) FROM policies"))).scalar_one()
    if base_version is not None and base_version != (latest or 0):
        raise AppError("E-CONF-001")
    row = (
        await session.execute(
            text(
                "INSERT INTO policies (version, status, content, content_hash, created_by)"
                " VALUES (:v, 'draft', CAST(:c AS jsonb), :h, :u) RETURNING " + _COLS
            ),
            {
                "v": (latest or 0) + 1,
                "c": json.dumps(content.model_dump(mode="json")),
                "h": content_hash(content),
                "u": principal.user_id,
            },
        )
    ).one()
    await record(
        session,
        AuditEvent(
            action="policy.draft_created",
            entity_type="policy",
            entity_id=str(row.id),
            actor_user_id=principal.user_id,
            actor_role=principal.role,
        ),
    )
    return _out(row)


async def update_draft(
    session: AsyncSession,
    principal: Principal,
    policy_id: uuid.UUID,
    content: AnyPolicy,
    base_version: int | None,
) -> PolicyOutAny:
    current = await _get(session, policy_id, lock=True)
    if current.status != "draft":
        raise AppError("E-POL-003")
    if base_version is not None and base_version != current.version:
        raise AppError("E-CONF-001")
    await _guard_content(session, content)
    row = (
        await session.execute(
            text(
                "UPDATE policies SET content = CAST(:c AS jsonb), content_hash = :h,"
                " regression_run_id = NULL WHERE id = :i RETURNING " + _COLS
            ),
            {
                "c": json.dumps(content.model_dump(mode="json")),
                "h": content_hash(content),
                "i": policy_id,
            },
        )
    ).one()
    await record(
        session,
        AuditEvent(
            action="policy.draft_updated",
            entity_type="policy",
            entity_id=str(policy_id),
            actor_user_id=principal.user_id,
            actor_role=principal.role,
        ),
    )
    return _out(row)


async def _run_and_store(
    session: AsyncSession, tenant_id: uuid.UUID, row: Any
) -> tuple[uuid.UUID, RegressionOut]:
    policy = parse_any(row.content)
    result = run_regression(policy)
    metrics = {
        "policy_id": str(row.id),
        "content_hash": row.content_hash,
        "scenarios_total": result.total,
        "scenarios_failed": result.failed,
        "eval_subset_passed": result.eval_subset_passed,
    }
    run_id = (
        await session.execute(
            text(
                "INSERT INTO eval_runs (kind, tenant_id, metrics, passed)"
                " VALUES ('policy_regression', :t, CAST(:m AS jsonb), :p) RETURNING id"
            ),
            {"t": tenant_id, "m": json.dumps(metrics), "p": result.passed},
        )
    ).scalar_one()
    return run_id, RegressionOut(
        run_id=run_id,
        passed=result.passed,
        scenarios_total=result.total,
        scenarios_failed=result.failed,
        eval_subset_passed=result.eval_subset_passed,
    )


async def regression(
    session: AsyncSession, principal: Principal, policy_id: uuid.UUID
) -> RegressionOut:
    row = await _get(session, policy_id)
    if principal.tenant_id is None:
        raise AppError("E-AUTH-007")
    run_id, out = await _run_and_store(session, principal.tenant_id, row)
    if row.status == "draft" and out.passed:
        await session.execute(
            text("UPDATE policies SET regression_run_id = :r WHERE id = :i"),
            {"r": run_id, "i": policy_id},
        )
    return out


async def publish(
    session: AsyncSession,
    principal: Principal,
    policy_id: uuid.UUID,
    *,
    admin_override: bool = False,
) -> PolicyOutAny:
    if principal.tenant_id is None:
        raise AppError("E-AUTH-007")
    await session.execute(text("SELECT id FROM tenants WHERE id = app_current_tenant() FOR UPDATE"))
    row = await _get(session, policy_id, lock=True)
    if row.status != "draft":
        raise AppError("E-POL-003")
    if not admin_override:
        await _guard_content(session, parse_any(row.content))
    run_id, out = await _run_and_store(session, principal.tenant_id, row)
    if not out.passed:
        raise AppError("E-POL-001", detail=",".join(out.scenarios_failed))
    await session.execute(
        text("UPDATE policies SET status = 'archived' WHERE status = 'published'")
    )
    published = (
        await session.execute(
            text(
                "UPDATE policies SET status = 'published', regression_run_id = :r,"
                " published_by = :u, published_at = :n WHERE id = :i RETURNING " + _COLS
            ),
            {"r": run_id, "u": principal.user_id, "n": timeutil.utc_now(), "i": policy_id},
        )
    ).one()
    await record(
        session,
        AuditEvent(
            action="policy.published",
            entity_type="policy",
            entity_id=str(policy_id),
            new_value={"version": published.version, "content_hash": published.content_hash},
            actor_user_id=principal.user_id,
            actor_role=principal.role,
        ),
    )
    return _out(published)
