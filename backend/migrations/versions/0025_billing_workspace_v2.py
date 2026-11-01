"""billing: cost categories (Cloud Run SKUs), Plivo rate settings, billing export config,
brand usage toggles + invoice terms, invoice due date and paid tracking"""
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

UP = [
    "ALTER TABLE billing_settings ADD COLUMN seller_pincode text NOT NULL DEFAULT ''"
    " CHECK (seller_pincode ~ '^([0-9]{6})?$')",
    "ALTER TABLE billing_settings ADD COLUMN billing_export_status text NOT NULL"
    " DEFAULT 'not_connected' CHECK (billing_export_status IN ('not_connected', 'connected'))",
    "ALTER TABLE billing_settings ADD COLUMN billing_export_ref text NOT NULL DEFAULT ''"
    " CHECK (length(billing_export_ref) <= 200)",
    "ALTER TABLE billing_settings ADD COLUMN reconciliation_cycle text NOT NULL"
    " DEFAULT 'monthly' CHECK (reconciliation_cycle IN"
    " ('monthly', 'daily_preview_monthly_final'))",
    "ALTER TABLE billing_settings ADD COLUMN unallocated_handling text NOT NULL"
    " DEFAULT 'keep_unallocated' CHECK (unallocated_handling IN"
    " ('keep_unallocated', 'manual_review'))",
    "ALTER TABLE billing_settings ADD COLUMN shared_allocation_policy text NOT NULL"
    " DEFAULT 'per_sku' CHECK (shared_allocation_policy IN ('per_sku', 'usage_weighted'))",
    "ALTER TABLE billing_settings ADD COLUMN plivo_outbound_inr_per_min numeric(10,4)"
    " NOT NULL DEFAULT 0 CHECK (plivo_outbound_inr_per_min >= 0)",
    "ALTER TABLE billing_settings ADD COLUMN plivo_inbound_inr_per_min numeric(10,4)"
    " NOT NULL DEFAULT 0 CHECK (plivo_inbound_inr_per_min >= 0)",
    "ALTER TABLE billing_settings ADD COLUMN plivo_number_rental_inr_per_month numeric(10,2)"
    " NOT NULL DEFAULT 0 CHECK (plivo_number_rental_inr_per_month >= 0)",
    """CREATE TABLE billing_cost_categories (
  category text PRIMARY KEY CHECK (length(category) BETWEEN 1 AND 100),
  unit text NOT NULL DEFAULT '' CHECK (length(unit) <= 40),
  usd_rate numeric(18,6) NOT NULL DEFAULT 0 CHECK (usd_rate >= 0),
  attribution text NOT NULL DEFAULT '' CHECK (length(attribution) <= 100),
  active boolean NOT NULL DEFAULT true,
  sort_order integer NOT NULL DEFAULT 0,
  updated_by uuid,
  updated_at timestamptz NOT NULL DEFAULT now()
)""",
    'ALTER TABLE "billing_cost_categories" ENABLE ROW LEVEL SECURITY',
    'ALTER TABLE "billing_cost_categories" FORCE ROW LEVEL SECURITY',
    'CREATE POLICY super_admin_only ON "billing_cost_categories"'
    " USING (app_is_super_admin()) WITH CHECK (app_is_super_admin())",
    "INSERT INTO billing_cost_categories (category, unit, attribution, sort_order) VALUES"
    " ('Services CPU', 'vCPU-second', 'Direct', 1),"
    " ('Services Memory', 'GiB-second', 'Direct', 2),"
    " ('Requests', 'Request / 1M', 'Direct / SKU', 3),"
    " ('Jobs CPU', 'vCPU-second', 'Direct', 4),"
    " ('Jobs Memory', 'GiB-second', 'Direct', 5),"
    " ('Network data transfer', 'GiB', 'Direct / shared', 6),"
    " ('Artifact Registry storage', 'GiB-month', 'Shared policy', 7),"
    " ('Artifact Registry egress', 'GiB', 'Direct / shared', 8),"
    " ('Cloud Logging storage', 'GiB', 'Client log bytes', 9),"
    " ('Secret Manager storage', 'Secret-month', 'Direct / shared', 10),"
    " ('Secret access operations', 'Operation', 'Direct', 11),"
    " ('Cloud Scheduler', 'Job / month', 'Direct / shared', 12)",
    "ALTER TABLE tenant_billing_profiles ADD COLUMN cloud_enabled boolean NOT NULL DEFAULT true",
    "ALTER TABLE tenant_billing_profiles ADD COLUMN llm_enabled boolean NOT NULL DEFAULT true",
    "ALTER TABLE tenant_billing_profiles ADD COLUMN plivo_enabled boolean NOT NULL DEFAULT true",
    "ALTER TABLE tenant_billing_profiles ADD COLUMN payment_due_days smallint NOT NULL"
    " DEFAULT 15 CHECK (payment_due_days BETWEEN 0 AND 120)",
    "ALTER TABLE tenant_billing_profiles ADD COLUMN auto_generate_draft boolean NOT NULL"
    " DEFAULT true",
    "ALTER TABLE tenant_billing_profiles ADD COLUMN billing_status text NOT NULL"
    " DEFAULT 'enabled' CHECK (billing_status IN ('enabled', 'paused'))",
    "ALTER TABLE invoices ADD COLUMN due_date date",
    "ALTER TABLE invoices ADD COLUMN paid_at timestamptz",
    "ALTER TABLE invoices ADD COLUMN paid_by uuid",
]

DOWN = [
    "ALTER TABLE invoices DROP COLUMN paid_by",
    "ALTER TABLE invoices DROP COLUMN paid_at",
    "ALTER TABLE invoices DROP COLUMN due_date",
    "ALTER TABLE tenant_billing_profiles DROP COLUMN billing_status",
    "ALTER TABLE tenant_billing_profiles DROP COLUMN auto_generate_draft",
    "ALTER TABLE tenant_billing_profiles DROP COLUMN payment_due_days",
    "ALTER TABLE tenant_billing_profiles DROP COLUMN plivo_enabled",
    "ALTER TABLE tenant_billing_profiles DROP COLUMN llm_enabled",
    "ALTER TABLE tenant_billing_profiles DROP COLUMN cloud_enabled",
    "DROP TABLE billing_cost_categories",
    "ALTER TABLE billing_settings DROP COLUMN plivo_number_rental_inr_per_month",
    "ALTER TABLE billing_settings DROP COLUMN plivo_inbound_inr_per_min",
    "ALTER TABLE billing_settings DROP COLUMN plivo_outbound_inr_per_min",
    "ALTER TABLE billing_settings DROP COLUMN shared_allocation_policy",
    "ALTER TABLE billing_settings DROP COLUMN unallocated_handling",
    "ALTER TABLE billing_settings DROP COLUMN reconciliation_cycle",
    "ALTER TABLE billing_settings DROP COLUMN billing_export_ref",
    "ALTER TABLE billing_settings DROP COLUMN billing_export_status",
    "ALTER TABLE billing_settings DROP COLUMN seller_pincode",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
