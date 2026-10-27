"""alerts: platform/tenant health alerts, admin managed, deduped by key"""
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def _admin_only(table: str) -> list[str]:
    return [
        f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY',
        f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY',
        f'CREATE POLICY super_admin_only ON "{table}"'
        " USING (app_is_super_admin()) WITH CHECK (app_is_super_admin())",
    ]


UP = [
    """CREATE TABLE alerts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid REFERENCES tenants(id),
  kind text NOT NULL CHECK (length(kind) BETWEEN 1 AND 60),
  severity text NOT NULL CHECK (severity IN ('info','warning','critical')),
  message text NOT NULL CHECK (length(message) BETWEEN 1 AND 500),
  dedupe_key text NOT NULL CHECK (length(dedupe_key) BETWEEN 1 AND 200),
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz,
  resolved_by uuid
)""",
    "CREATE UNIQUE INDEX ux_alerts_open_dedupe ON alerts (dedupe_key) WHERE status = 'open'",
    "CREATE INDEX ix_alerts_status_time ON alerts (status, created_at DESC)",
    *_admin_only("alerts"),
]

DOWN = [
    "DROP TABLE alerts",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
