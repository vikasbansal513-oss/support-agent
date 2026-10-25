import pytest

from app.core.tenancy import tenant_rls_statements


def test_statements_enable_force_policy() -> None:
    statements = tenant_rls_statements("jobs")
    assert statements[0] == 'ALTER TABLE "jobs" ENABLE ROW LEVEL SECURITY'
    assert statements[1] == 'ALTER TABLE "jobs" FORCE ROW LEVEL SECURITY'
    assert "WITH CHECK (tenant_id = app_current_tenant())" in statements[2]
    assert len(statements) == 3


def test_super_admin_read_adds_select_policy() -> None:
    statements = tenant_rls_statements("jobs", super_admin_read=True)
    assert len(statements) == 4
    assert "FOR SELECT USING (app_is_super_admin())" in statements[3]


def test_invalid_table_name_rejected() -> None:
    with pytest.raises(ValueError):
        tenant_rls_statements("jobs; DROP TABLE users")
