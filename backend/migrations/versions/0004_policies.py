"""eval_runs (moved up from 0009 so policy publish can reference it) and policies"""
from alembic import op

from app.core.tenancy import tenant_rls_statements

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UP = [
    """CREATE TABLE eval_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind text NOT NULL CHECK (kind IN ('eval','policy_regression','safety')),
  tenant_id uuid REFERENCES tenants(id),
  git_sha text, prompt_versions jsonb, model text, metrics jsonb NOT NULL, passed boolean NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
)""",
    "ALTER TABLE eval_runs ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE eval_runs FORCE ROW LEVEL SECURITY",
    """CREATE POLICY eval_runs_rls ON eval_runs USING (tenant_id = app_current_tenant() OR app_is_super_admin())
  WITH CHECK (tenant_id = app_current_tenant() OR app_is_super_admin())""",
    """CREATE TABLE policies (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  version integer NOT NULL CHECK (version >= 1),
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','archived')),
  content jsonb NOT NULL, content_hash text NOT NULL,
  created_by uuid, created_at timestamptz NOT NULL DEFAULT now(),
  regression_run_id uuid, published_by uuid, published_at timestamptz,
  UNIQUE (tenant_id, version), UNIQUE (tenant_id, id),
  CHECK (status <> 'published' OR (published_by IS NOT NULL AND published_at IS NOT NULL AND regression_run_id IS NOT NULL))
)""",
    "CREATE UNIQUE INDEX ux_policies_one_published ON policies (tenant_id) WHERE status = 'published'",
    "ALTER TABLE policies ADD CONSTRAINT fk_policies_regression FOREIGN KEY (regression_run_id) REFERENCES eval_runs(id)",
    *tenant_rls_statements("policies", super_admin_read=True),
]

DOWN = [
    "DROP TABLE policies",
    "DROP TABLE eval_runs",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
