"""consents, privacy_requests, platform_flags, tenants.legal_hold"""
from alembic import op

from app.core.tenancy import tenant_rls_statements

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

UP = [
    """CREATE TABLE consents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  subject_type text NOT NULL CHECK (subject_type IN ('customer','user')), subject_id uuid NOT NULL,
  purpose text NOT NULL CHECK (purpose IN ('whatsapp_optin','ai_processing','email_contact')),
  notice_version text NOT NULL, granted boolean NOT NULL, source text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
)""",
    "CREATE INDEX ix_consents_subject ON consents (tenant_id, subject_type, subject_id, purpose,"
    " created_at DESC)",
    "CREATE TRIGGER consents_no_row_mutation BEFORE UPDATE OR DELETE ON consents"
    " FOR EACH ROW EXECUTE FUNCTION append_only()",
    "CREATE TRIGGER consents_no_truncate BEFORE TRUNCATE ON consents"
    " FOR EACH STATEMENT EXECUTE FUNCTION append_only()",
    "REVOKE UPDATE, DELETE, TRUNCATE ON consents FROM support_app_runtime",
    *tenant_rls_statements("consents"),
    """CREATE TABLE privacy_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  subject_ref text NOT NULL, kind text NOT NULL CHECK (kind IN ('export','delete','correct')),
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','done','rejected')),
  due_at timestamptz NOT NULL DEFAULT now() + interval '30 days',
  created_at timestamptz NOT NULL DEFAULT now(), done_at timestamptz,
  created_by uuid, note text
)""",
    "CREATE UNIQUE INDEX ux_privacy_one_open ON privacy_requests (tenant_id, subject_ref, kind)"
    " WHERE status = 'open'",
    *tenant_rls_statements("privacy_requests", super_admin_read=True),
    "ALTER TABLE tenants ADD COLUMN legal_hold boolean NOT NULL DEFAULT false",
    """CREATE TABLE platform_flags (
  name text PRIMARY KEY, enabled boolean NOT NULL DEFAULT false, reason text,
  updated_by uuid, updated_at timestamptz NOT NULL DEFAULT now()
)""",
    "INSERT INTO platform_flags (name, enabled) VALUES ('global_pause', false)",
]

DOWN = [
    "DROP TABLE platform_flags",
    "ALTER TABLE tenants DROP COLUMN legal_hold",
    "DROP TABLE privacy_requests",
    "DROP TABLE consents",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
