"""foundation: runtime role, tenant context functions, audit_log"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UP = [
    """
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'support_app_runtime') THEN
    CREATE ROLE support_app_runtime NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'support_app_runtime' AND (rolsuper OR rolbypassrls)) THEN
    RAISE EXCEPTION 'runtime role must not be superuser/BYPASSRLS';
  END IF;
END $$
""",
    "GRANT USAGE ON SCHEMA public TO support_app_runtime",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO support_app_runtime",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO support_app_runtime",
    """CREATE FUNCTION app_current_tenant() RETURNS uuid LANGUAGE sql STABLE AS
$$ SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid $$""",
    """CREATE FUNCTION app_current_user() RETURNS uuid LANGUAGE sql STABLE AS
$$ SELECT NULLIF(current_setting('app.user_id', true), '')::uuid $$""",
    """CREATE FUNCTION app_is_super_admin() RETURNS boolean LANGUAGE sql STABLE AS
$$ SELECT COALESCE(NULLIF(current_setting('app.is_super_admin', true), ''), 'off') = 'on' $$""",
    "GRANT EXECUTE ON FUNCTION app_current_tenant(), app_current_user(), app_is_super_admin() TO support_app_runtime",
    """CREATE TABLE audit_log (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  occurred_at timestamptz NOT NULL DEFAULT now(),
  tenant_id uuid DEFAULT app_current_tenant(),
  actor_user_id uuid, actor_role text,
  action text NOT NULL CHECK (length(btrim(action)) > 0),
  entity_type text, entity_id text, old_value jsonb, new_value jsonb, reason text, source_ref text, run_id text
)""",
    "CREATE INDEX ix_audit_log_tenant_time ON audit_log (tenant_id, occurred_at DESC)",
    "CREATE INDEX ix_audit_log_action ON audit_log (action, occurred_at DESC)",
    "ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE audit_log FORCE ROW LEVEL SECURITY",
    "CREATE POLICY audit_select ON audit_log FOR SELECT USING (tenant_id = app_current_tenant() OR app_is_super_admin())",
    """CREATE POLICY audit_insert ON audit_log FOR INSERT
  WITH CHECK (tenant_id IS NOT DISTINCT FROM app_current_tenant() OR app_is_super_admin())""",
    """CREATE FUNCTION append_only() RETURNS trigger LANGUAGE plpgsql AS
$$ BEGIN RAISE EXCEPTION '% is append-only: UPDATE/DELETE/TRUNCATE not allowed', TG_TABLE_NAME; END $$""",
    "CREATE TRIGGER audit_log_no_row_mutation BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW EXECUTE FUNCTION append_only()",
    "CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log FOR EACH STATEMENT EXECUTE FUNCTION append_only()",
    "REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM support_app_runtime",
]

DOWN = [
    "DROP TABLE audit_log",
    "DROP FUNCTION append_only()",
    "DROP FUNCTION app_is_super_admin()",
    "DROP FUNCTION app_current_user()",
    "DROP FUNCTION app_current_tenant()",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
