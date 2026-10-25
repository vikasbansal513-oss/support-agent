"""customers, conversations, inbound_events, messages, usage_counters"""
from alembic import op

from app.core.tenancy import tenant_rls_statements

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

UP = [
    """CREATE TABLE customers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  phone_e164 text CHECK (phone_e164 ~ '^\\+[1-9][0-9]{7,14}$'),
  email_lower text CHECK (email_lower = lower(email_lower)),
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (phone_e164 IS NOT NULL OR email_lower IS NOT NULL),
  UNIQUE (tenant_id, id)
)""",
    "CREATE UNIQUE INDEX ux_customers_phone ON customers (tenant_id, phone_e164)"
    " WHERE phone_e164 IS NOT NULL",
    "CREATE UNIQUE INDEX ux_customers_email ON customers (tenant_id, email_lower)"
    " WHERE email_lower IS NOT NULL",
    *tenant_rls_statements("customers"),
    """CREATE TABLE conversations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  customer_id uuid NOT NULL,
  channel text NOT NULL CHECK (channel IN ('whatsapp','email')),
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','handoff','resolved','closed')),
  active_order_ref text,
  identity_attempts integer NOT NULL DEFAULT 0 CHECK (identity_attempts >= 0),
  language text CHECK (language IN ('hi','en','hinglish')),
  human_active boolean NOT NULL DEFAULT false,
  counted_for_usage boolean NOT NULL DEFAULT false,
  last_inbound_at timestamptz, last_message_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  FOREIGN KEY (tenant_id, customer_id) REFERENCES customers (tenant_id, id)
)""",
    "CREATE INDEX ix_conv_tenant_last ON conversations (tenant_id, last_message_at DESC)",
    "CREATE UNIQUE INDEX ux_conv_one_open ON conversations (tenant_id, customer_id, channel)"
    " WHERE status IN ('open','handoff')",
    *tenant_rls_statements("conversations", super_admin_read=True),
    """CREATE TABLE inbound_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  channel text NOT NULL CHECK (channel IN ('whatsapp','email')),
  external_id text NOT NULL,
  raw_payload jsonb NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  status text NOT NULL DEFAULT 'received'
    CHECK (status IN ('received','queued','processed','skipped','needs_review')),
  skip_reason text,
  UNIQUE (tenant_id, channel, external_id), UNIQUE (tenant_id, id)
)""",
    *tenant_rls_statements("inbound_events"),
    """CREATE TABLE messages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  conversation_id uuid NOT NULL,
  direction text NOT NULL CHECK (direction IN ('in','out')),
  sender text NOT NULL CHECK (sender IN ('customer','agent','human','system')),
  channel text NOT NULL CHECK (channel IN ('whatsapp','email')),
  content text NOT NULL, external_id text, inbound_event_id uuid, action_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations (tenant_id, id),
  FOREIGN KEY (tenant_id, inbound_event_id) REFERENCES inbound_events (tenant_id, id)
)""",
    "CREATE INDEX ix_messages_conv ON messages (tenant_id, conversation_id, created_at)",
    *tenant_rls_statements("messages"),
    """CREATE TABLE usage_counters (
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  period_start date NOT NULL,
  conversation_count integer NOT NULL DEFAULT 0 CHECK (conversation_count >= 0),
  PRIMARY KEY (tenant_id, period_start)
)""",
    *tenant_rls_statements("usage_counters", super_admin_read=True),
]

DOWN = [
    "DROP TABLE usage_counters",
    "DROP TABLE messages",
    "DROP TABLE inbound_events",
    "DROP TABLE conversations",
    "DROP TABLE customers",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
