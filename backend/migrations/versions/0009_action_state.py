"""agent_actions: V1 write-action columns and statuses"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

STATUSES_V1 = (
    "claimed",
    "shadow",
    "draft",
    "sent",
    "failed",
    "needs_review",
    "rejected",
    "suppressed",
    "proposed",
    "awaiting_customer",
    "pending_approval",
    "approved",
    "executed",
    "confirmed",
    "expired",
)
STATUSES_V0 = STATUSES_V1[:8]


def _check(values: tuple[str, ...]) -> str:
    listed = ",".join(f"'{v}'" for v in values)
    return f"ALTER TABLE agent_actions ADD CONSTRAINT agent_actions_status_check CHECK (status IN ({listed}))"


UP = [
    "ALTER TABLE agent_actions DROP CONSTRAINT agent_actions_status_check",
    _check(STATUSES_V1),
    "ALTER TABLE agent_actions ADD COLUMN risk_tier text CHECK (risk_tier IN ('T0','T1','T2','T3'))",
    "ALTER TABLE agent_actions ADD COLUMN order_ref text",
    "ALTER TABLE agent_actions ADD COLUMN amount_paise bigint CHECK (amount_paise >= 0)",
    "ALTER TABLE agent_actions ADD COLUMN requires_approval boolean NOT NULL DEFAULT false",
    "ALTER TABLE agent_actions ADD COLUMN approved_by uuid",
    "ALTER TABLE agent_actions ADD COLUMN approved_at timestamptz",
    "ALTER TABLE agent_actions ADD COLUMN confirm_token_hash text",
    "ALTER TABLE agent_actions ADD COLUMN confirm_expires_at timestamptz",
    "CREATE UNIQUE INDEX ux_actions_one_active_write ON agent_actions (tenant_id, conversation_id)"
    " WHERE action_type = 'tool_call' AND risk_tier IN ('T1','T2','T3') AND status IN"
    " ('proposed','awaiting_customer','pending_approval','approved','claimed','executed')",
    "CREATE INDEX ix_actions_pending ON agent_actions (tenant_id, created_at DESC)"
    " WHERE status = 'pending_approval'",
]

DOWN = [
    "DROP INDEX ix_actions_pending",
    "DROP INDEX ux_actions_one_active_write",
    "ALTER TABLE agent_actions DROP COLUMN confirm_expires_at, DROP COLUMN confirm_token_hash,"
    " DROP COLUMN approved_at, DROP COLUMN approved_by, DROP COLUMN requires_approval,"
    " DROP COLUMN amount_paise, DROP COLUMN order_ref, DROP COLUMN risk_tier",
    "ALTER TABLE agent_actions DROP CONSTRAINT agent_actions_status_check",
    _check(STATUSES_V0),
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
