"""action_usage: widen tool check to include return_request"""
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

_FIND_CHECK = """DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT conname FROM pg_constraint
    WHERE contype = 'c' AND conrelid = 'action_usage'::regclass
      AND pg_get_constraintdef(oid) ~ 'tool = ANY'
  LOOP
    EXECUTE format('ALTER TABLE action_usage DROP CONSTRAINT %I', r.conname);
  END LOOP;
END $$"""

_OLD_TOOLS = "'address_update','order_cancel','exchange_request','refund_create'"
_NEW_TOOLS = "'address_update','order_cancel','exchange_request','return_request','refund_create'"

UP = [
    _FIND_CHECK,
    f"ALTER TABLE action_usage ADD CONSTRAINT action_usage_tool_check CHECK (tool IN ({_NEW_TOOLS}))",
]

DOWN = [
    "DELETE FROM action_usage WHERE tool = 'return_request'",
    _FIND_CHECK,
    f"ALTER TABLE action_usage ADD CONSTRAINT action_usage_tool_check CHECK (tool IN ({_OLD_TOOLS}))",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
