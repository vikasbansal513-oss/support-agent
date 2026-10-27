"""billing: telephony (Plivo calling) actual-bill line, allocated by call seconds"""
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

_FIND_CHECK = """DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT conname FROM pg_constraint
    WHERE contype = 'c' AND conrelid = '{table}'::regclass
      AND pg_get_constraintdef(oid) ~ '{marker}'
  LOOP
    EXECUTE format('ALTER TABLE {table} DROP CONSTRAINT %I', r.conname);
  END LOOP;
END $$"""

UP = [
    "ALTER TABLE billing_periods ADD COLUMN telephony_actual_paise bigint"
    " CHECK (telephony_actual_paise IS NULL OR telephony_actual_paise >= 0)",
    # voice_calls had only tenant-isolation RLS (no super-admin SELECT bypass), unlike jobs;
    # billing needs to read call durations across all tenants for one admin-only aggregate.
    'CREATE POLICY super_admin_read ON "voice_calls" FOR SELECT USING (app_is_super_admin())',
    "ALTER TABLE invoices ADD COLUMN telephony_paise bigint NOT NULL DEFAULT 0"
    " CHECK (telephony_paise >= 0)",
    _FIND_CHECK.format(table="invoices", marker="subtotal_paise = "),
    "ALTER TABLE invoices ADD CONSTRAINT invoices_subtotal_check"
    " CHECK (subtotal_paise = llm_paise + cloudrun_paise + telephony_paise + platform_fee_paise)",
    _FIND_CHECK.format(table="invoice_lines", marker="kind = ANY"),
    "ALTER TABLE invoice_lines ADD CONSTRAINT invoice_lines_kind_check"
    " CHECK (kind IN ('llm','cloudrun','telephony','platform_fee'))",
]

DOWN = [
    _FIND_CHECK.format(table="invoice_lines", marker="kind = ANY"),
    "ALTER TABLE invoice_lines ADD CONSTRAINT invoice_lines_kind_check"
    " CHECK (kind IN ('llm','cloudrun','platform_fee'))",
    _FIND_CHECK.format(table="invoices", marker="subtotal_paise = "),
    "ALTER TABLE invoices DROP COLUMN telephony_paise",
    "ALTER TABLE invoices ADD CONSTRAINT invoices_subtotal_check"
    " CHECK (subtotal_paise = llm_paise + cloudrun_paise + platform_fee_paise)",
    'DROP POLICY super_admin_read ON "voice_calls"',
    "ALTER TABLE billing_periods DROP COLUMN telephony_actual_paise",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
