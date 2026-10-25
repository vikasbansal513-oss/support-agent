"""conversation_state for multi-turn planning"""
from alembic import op

from app.core.tenancy import tenant_rls_statements

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

UP = [
    """CREATE TABLE conversation_state (
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  conversation_id uuid NOT NULL,
  pending_action_id uuid,
  awaiting text NOT NULL DEFAULT 'none' CHECK (awaiting IN ('confirm','address','order_ref','none')),
  turn_count integer NOT NULL DEFAULT 0 CHECK (turn_count >= 0),
  unclear_count integer NOT NULL DEFAULT 0 CHECK (unclear_count >= 0),
  write_count integer NOT NULL DEFAULT 0 CHECK (write_count >= 0),
  context jsonb NOT NULL DEFAULT '{}',
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, conversation_id),
  FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations (tenant_id, id),
  FOREIGN KEY (tenant_id, pending_action_id) REFERENCES agent_actions (tenant_id, id)
)""",
    *tenant_rls_statements("conversation_state"),
]

DOWN = ["DROP TABLE conversation_state"]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
