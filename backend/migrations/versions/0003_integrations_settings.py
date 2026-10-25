"""integrations, wa_numbers, tenant_settings, external_calls"""
from alembic import op

from app.core.tenancy import tenant_rls_statements

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UP = [
    """CREATE TABLE integrations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  kind text NOT NULL CHECK (kind IN ('shopify','gupshup','gmail','shiprocket','delhivery')),
  config jsonb NOT NULL DEFAULT '{}',
  secret_enc text,
  webhook_secret_hash text,
  status text NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','auth_error','disabled')),
  last_error_code text, last_checked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, kind)
)""",
    *tenant_rls_statements("integrations", super_admin_read=True),
    """CREATE TABLE wa_numbers (
  source_number text PRIMARY KEY, app_name text NOT NULL DEFAULT '',
  webhook_token_hash text NOT NULL UNIQUE CHECK (webhook_token_hash ~ '^[0-9a-f]{64}$'),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() UNIQUE REFERENCES tenants(id)
)""",
    "ALTER TABLE wa_numbers ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE wa_numbers FORCE ROW LEVEL SECURITY",
    "CREATE POLICY wa_lookup ON wa_numbers FOR SELECT USING (true)",
    "CREATE POLICY wa_write_ins ON wa_numbers FOR INSERT WITH CHECK (tenant_id = app_current_tenant())",
    """CREATE POLICY wa_write_upd ON wa_numbers FOR UPDATE USING (tenant_id = app_current_tenant())
  WITH CHECK (tenant_id = app_current_tenant())""",
    "CREATE POLICY wa_write_del ON wa_numbers FOR DELETE USING (tenant_id = app_current_tenant())",
    """CREATE TABLE tenant_settings (
  tenant_id uuid PRIMARY KEY DEFAULT app_current_tenant() REFERENCES tenants(id),
  notify_email_enabled boolean NOT NULL DEFAULT true, notify_email text,
  notify_whatsapp_enabled boolean NOT NULL DEFAULT false, alert_phone text,
  grievance_email text, timezone text NOT NULL DEFAULT 'Asia/Kolkata',
  updated_at timestamptz NOT NULL DEFAULT now()
)""",
    *tenant_rls_statements("tenant_settings"),
    """CREATE TABLE external_calls (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  run_id uuid, provider text NOT NULL, operation text NOT NULL, request_summary jsonb,
  http_status integer, response_body_redacted text CHECK (length(response_body_redacted) <= 2048),
  error_class text, latency_ms integer, created_at timestamptz NOT NULL DEFAULT now()
)""",
    "CREATE INDEX ix_extcalls_tenant_time ON external_calls (tenant_id, created_at DESC)",
    *tenant_rls_statements("external_calls"),
]

DOWN = [
    "DROP TABLE external_calls",
    "DROP TABLE tenant_settings",
    "DROP TABLE wa_numbers",
    "DROP TABLE integrations",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
