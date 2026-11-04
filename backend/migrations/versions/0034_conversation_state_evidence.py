"""allow 'evidence' in conversation_state.awaiting (Section 12.3 evidence-ask flow)"""
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

UP = [
    "ALTER TABLE conversation_state DROP CONSTRAINT conversation_state_awaiting_check",
    "ALTER TABLE conversation_state ADD CONSTRAINT conversation_state_awaiting_check"
    " CHECK (awaiting IN ('confirm','address','order_ref','evidence','none'))",
]

DOWN = [
    "ALTER TABLE conversation_state DROP CONSTRAINT conversation_state_awaiting_check",
    "ALTER TABLE conversation_state ADD CONSTRAINT conversation_state_awaiting_check"
    " CHECK (awaiting IN ('confirm','address','order_ref','none'))",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
