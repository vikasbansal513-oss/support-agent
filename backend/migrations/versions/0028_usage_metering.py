"""Usage metering: per-client usage_limits (admin sets these for any tenant, like
tenant_billing_profiles -- admin-only RLS, not tenant isolation). 70/90/100% breach alerts
reuse the existing `alerts` table (see core/alerts.py), no new alerts table. billing_settings
gets sync-status columns for the GCP Billing Export (BigQuery) import that already exists
(gcp_billing_import_rows/gcp_reconciliation_runs since 0026) -- no new import-history table,
grouping gcp_billing_import_rows by import_batch_id IS the history."""
from alembic import op

revision = "0028"
down_revision = "0027"
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
    "ALTER TABLE billing_settings ADD COLUMN billing_export_last_sync_at timestamptz",
    "ALTER TABLE billing_settings ADD COLUMN billing_export_last_sync_rows integer"
    " NOT NULL DEFAULT 0 CHECK (billing_export_last_sync_rows >= 0)",
    "ALTER TABLE billing_settings ADD COLUMN billing_export_sync_error text"
    " NOT NULL DEFAULT '' CHECK (length(billing_export_sync_error) <= 500)",
    """CREATE TABLE usage_limits (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  metric text NOT NULL CHECK (metric IN ('messages_month', 'total_cost_inr_month',
    'llm_cost_inr_month')),
  limit_value numeric(18,2) NOT NULL CHECK (limit_value > 0),
  active boolean NOT NULL DEFAULT true,
  updated_by uuid,
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, metric)
)""",
    "CREATE INDEX ix_usage_limits_tenant ON usage_limits (tenant_id)",
    *_admin_only("usage_limits"),
]

DOWN = [
    "DROP TABLE usage_limits",
    "ALTER TABLE billing_settings DROP COLUMN billing_export_sync_error",
    "ALTER TABLE billing_settings DROP COLUMN billing_export_last_sync_rows",
    "ALTER TABLE billing_settings DROP COLUMN billing_export_last_sync_at",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
