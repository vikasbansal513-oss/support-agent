"""inbound_events: processed_at (set when the agent's reply is written), for accurate
voice call-detail reply timestamps - received_at/created_at only record when the inbound
event itself arrived, not when the agent actually replied to it."""
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

UP = [
    "ALTER TABLE inbound_events ADD COLUMN processed_at timestamptz",
]

DOWN = [
    "ALTER TABLE inbound_events DROP COLUMN processed_at",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
