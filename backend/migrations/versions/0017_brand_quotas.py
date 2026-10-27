"""brand_quotas: per-tenant monthly AI-call limit, admin managed"""
from alembic import op

revision = "0017"
down_revision = "0016"
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
    """CREATE TABLE brand_quotas (
  tenant_id uuid PRIMARY KEY DEFAULT app_current_tenant() REFERENCES tenants(id),
  monthly_call_limit integer CHECK (monthly_call_limit IS NULL OR monthly_call_limit >= 0),
  warn_percent integer NOT NULL DEFAULT 80 CHECK (warn_percent BETWEEN 1 AND 100),
  action_on_limit text NOT NULL DEFAULT 'notify_only' CHECK (action_on_limit = 'notify_only'),
  updated_by uuid,
  updated_at timestamptz NOT NULL DEFAULT now()
)""",
    *_admin_only("brand_quotas"),
]

DOWN = [
    "DROP TABLE brand_quotas",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
