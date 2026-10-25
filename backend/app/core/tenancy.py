from contextvars import ContextVar
from uuid import UUID

current_tenant: ContextVar[UUID | None] = ContextVar("current_tenant", default=None)
current_super_admin: ContextVar[bool] = ContextVar("current_super_admin", default=False)
current_user: ContextVar[UUID | None] = ContextVar("current_user", default=None)

TENANT_POLICY_NAME = "tenant_isolation"
SUPER_ADMIN_READ_POLICY_NAME = "super_admin_read"


def require_tenant() -> UUID:
    tenant_id = current_tenant.get()
    if tenant_id is None:
        raise RuntimeError("tenant context is not set")
    return tenant_id


def tenant_rls_statements(table: str, super_admin_read: bool = False) -> list[str]:
    if not table.isidentifier():
        raise ValueError(f"invalid table name: {table!r}")
    statements = [
        f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY',
        f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY',
        f'CREATE POLICY {TENANT_POLICY_NAME} ON "{table}" '
        "USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())",
    ]
    if super_admin_read:
        statements.append(
            f'CREATE POLICY {SUPER_ADMIN_READ_POLICY_NAME} ON "{table}" '
            "FOR SELECT USING (app_is_super_admin())"
        )
    return statements
