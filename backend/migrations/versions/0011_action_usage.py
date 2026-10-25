"""action_usage: per customer, day and tool counters for limits"""
from alembic import op

from app.core.tenancy import tenant_rls_statements

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

UP = [
    """CREATE TABLE action_usage (
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  customer_id uuid NOT NULL,
  day date NOT NULL,
  tool text NOT NULL CHECK (tool IN ('address_update','order_cancel','exchange_request','refund_create')),
  count integer NOT NULL DEFAULT 0 CHECK (count >= 0),
  sum_paise bigint NOT NULL DEFAULT 0 CHECK (sum_paise >= 0),
  PRIMARY KEY (tenant_id, customer_id, day, tool),
  FOREIGN KEY (tenant_id, customer_id) REFERENCES customers (tenant_id, id)
)""",
    "CREATE INDEX ix_action_usage_day ON action_usage (tenant_id, day)",
    *tenant_rls_statements("action_usage"),
]

DOWN = ["DROP TABLE action_usage"]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
