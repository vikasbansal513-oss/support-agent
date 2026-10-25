"""voice channel: voice_numbers, voice_calls, channel and consent purpose values"""
from alembic import op

from app.core.tenancy import tenant_rls_statements

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

CHANNEL_TABLES = ("conversations", "messages", "inbound_events", "opt_outs")

DROP_CHECKS = """DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT conrelid::regclass::text AS tbl, conname FROM pg_constraint
    WHERE contype = 'c' AND conrelid::regclass::text = ANY(ARRAY[{tables}])
      AND pg_get_constraintdef(oid) ~ '{column}'
  LOOP
    EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', r.tbl, r.conname);
  END LOOP;
END $$"""

UP = [
    DROP_CHECKS.format(
        tables=", ".join(f"'{t}'" for t in CHANNEL_TABLES), column="channel = ANY"
    ),
    *[
        f"ALTER TABLE {t} ADD CONSTRAINT {t}_channel_check CHECK (channel IN ('whatsapp','email','voice'))"
        for t in CHANNEL_TABLES
    ],
    DROP_CHECKS.format(tables="'consents'", column="purpose"),
    """ALTER TABLE consents ADD CONSTRAINT consents_purpose_check
  CHECK (purpose IN ('whatsapp_optin','ai_processing','email_contact','voice_contact'))""",
    """CREATE TABLE voice_numbers (
  called_number text PRIMARY KEY CHECK (called_number ~ '^\\+[1-9][0-9]{7,14}$'),
  webhook_token_hash text NOT NULL UNIQUE CHECK (webhook_token_hash ~ '^[0-9a-f]{64}$'),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() UNIQUE REFERENCES tenants(id),
  enabled boolean NOT NULL DEFAULT true,
  transfer_number text CHECK (transfer_number ~ '^\\+[1-9][0-9]{7,14}$'),
  updated_at timestamptz NOT NULL DEFAULT now()
)""",
    "ALTER TABLE voice_numbers ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE voice_numbers FORCE ROW LEVEL SECURITY",
    "CREATE POLICY voice_lookup ON voice_numbers FOR SELECT USING (true)",
    "CREATE POLICY voice_write_ins ON voice_numbers FOR INSERT WITH CHECK (tenant_id = app_current_tenant())",
    """CREATE POLICY voice_write_upd ON voice_numbers FOR UPDATE USING (tenant_id = app_current_tenant())
  WITH CHECK (tenant_id = app_current_tenant())""",
    "CREATE POLICY voice_write_del ON voice_numbers FOR DELETE USING (tenant_id = app_current_tenant())",
    """CREATE TABLE voice_calls (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  provider_call_id text NOT NULL CHECK (length(provider_call_id) BETWEEN 1 AND 200),
  conversation_id uuid NOT NULL,
  customer_id uuid NOT NULL,
  caller_masked text,
  status text NOT NULL DEFAULT 'in_progress'
    CHECK (status IN ('in_progress','completed','transferred','callback','failed')),
  turn_count integer NOT NULL DEFAULT 0 CHECK (turn_count >= 0),
  low_confidence_streak integer NOT NULL DEFAULT 0 CHECK (low_confidence_streak >= 0),
  silence_streak integer NOT NULL DEFAULT 0 CHECK (silence_streak >= 0),
  end_reason text,
  started_at timestamptz NOT NULL DEFAULT now(),
  ended_at timestamptz,
  UNIQUE (tenant_id, provider_call_id),
  UNIQUE (tenant_id, id),
  FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations (tenant_id, id),
  FOREIGN KEY (tenant_id, customer_id) REFERENCES customers (tenant_id, id)
)""",
    "CREATE INDEX ix_voice_calls_time ON voice_calls (tenant_id, started_at DESC)",
    *tenant_rls_statements("voice_calls"),
]

DOWN = [
    "DROP TABLE voice_calls",
    "DROP TABLE voice_numbers",
    *[f"DELETE FROM {t} WHERE channel = 'voice'" for t in CHANNEL_TABLES],
    "DELETE FROM consents WHERE purpose = 'voice_contact'",
    *[f"ALTER TABLE {t} DROP CONSTRAINT {t}_channel_check" for t in CHANNEL_TABLES],
    *[
        f"ALTER TABLE {t} ADD CONSTRAINT {t}_channel_check CHECK (channel IN ('whatsapp','email'))"
        for t in CHANNEL_TABLES
    ],
    "ALTER TABLE consents DROP CONSTRAINT consents_purpose_check",
    """ALTER TABLE consents ADD CONSTRAINT consents_purpose_check
  CHECK (purpose IN ('whatsapp_optin','ai_processing','email_contact'))""",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
