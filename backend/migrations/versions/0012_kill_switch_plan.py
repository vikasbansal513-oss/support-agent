"""tenants.tool_kill, auto_enabled_*; agent_runs.plan, reply_attempts"""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

UP = [
    "ALTER TABLE tenants ADD COLUMN tool_kill jsonb NOT NULL DEFAULT '{}'",
    "ALTER TABLE tenants ADD COLUMN auto_enabled_by uuid",
    "ALTER TABLE tenants ADD COLUMN auto_enabled_at timestamptz",
    "ALTER TABLE agent_runs ADD COLUMN plan jsonb",
    "ALTER TABLE agent_runs ADD COLUMN reply_attempts integer NOT NULL DEFAULT 0",
]

DOWN = [
    "ALTER TABLE agent_runs DROP COLUMN reply_attempts, DROP COLUMN plan",
    "ALTER TABLE tenants DROP COLUMN auto_enabled_at, DROP COLUMN auto_enabled_by,"
    " DROP COLUMN tool_kill",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
