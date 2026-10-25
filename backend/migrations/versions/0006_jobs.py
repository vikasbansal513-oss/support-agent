"""jobs, notifications, heartbeats, opt_outs"""
from alembic import op

from app.core.tenancy import tenant_rls_statements

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

UP = [
    """CREATE TABLE jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  kind text NOT NULL,
  queue text NOT NULL DEFAULT 'default' CHECK (queue IN ('default','large')),
  payload jsonb NOT NULL DEFAULT '{}', idempotency_key text,
  status text NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued','deferred','processing','done','failed','needs_review')),
  attempts integer NOT NULL DEFAULT 0, max_attempts integer NOT NULL DEFAULT 3,
  run_after timestamptz NOT NULL DEFAULT now(),
  lease_owner text, lease_expires_at timestamptz, started_at timestamptz, finished_at timestamptz,
  duration_ms bigint NOT NULL DEFAULT 0, result jsonb,
  error_code text, error_message text, error_id text, created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (status <> 'processing' OR (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL))
)""",
    "CREATE UNIQUE INDEX ux_jobs_idem ON jobs (tenant_id, idempotency_key)"
    " WHERE idempotency_key IS NOT NULL",
    "CREATE INDEX ix_jobs_claim ON jobs (status, run_after) WHERE status IN ('queued','deferred')",
    *tenant_rls_statements("jobs", super_admin_read=True),
    "CREATE POLICY super_admin_write ON jobs FOR ALL USING (app_is_super_admin())"
    " WITH CHECK (app_is_super_admin())",
    """CREATE TABLE notifications (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  audience text NOT NULL DEFAULT 'admin', title text NOT NULL, body text, link text,
  read_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
)""",
    *tenant_rls_statements("notifications", super_admin_read=True),
    "CREATE TABLE heartbeats (name text PRIMARY KEY, last_seen_at timestamptz NOT NULL DEFAULT now())",
    """CREATE TABLE opt_outs (
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  customer_id uuid NOT NULL, channel text NOT NULL CHECK (channel IN ('whatsapp','email')),
  source text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, customer_id, channel),
  FOREIGN KEY (tenant_id, customer_id) REFERENCES customers (tenant_id, id)
)""",
    *tenant_rls_statements("opt_outs"),
]

DOWN = [
    "DROP TABLE opt_outs",
    "DROP TABLE heartbeats",
    "DROP TABLE notifications",
    "DROP TABLE jobs",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
