"""voice: telephony_mode (ready_made vs diy_plivo) on voice_numbers"""
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

UP = [
    "ALTER TABLE voice_numbers ADD COLUMN telephony_mode text NOT NULL DEFAULT 'ready_made'"
    " CHECK (telephony_mode IN ('ready_made', 'diy_plivo'))",
]

DOWN = [
    "ALTER TABLE voice_numbers DROP COLUMN telephony_mode",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
