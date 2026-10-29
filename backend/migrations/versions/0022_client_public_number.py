"""voice: client's own public/business number + carrier + forwarding_confirmed on voice_numbers"""
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

UP = [
    "ALTER TABLE voice_numbers ADD COLUMN client_public_number text",
    "ALTER TABLE voice_numbers ADD COLUMN carrier text"
    " CHECK (carrier IS NULL OR carrier IN ('jio', 'airtel', 'vi', 'bsnl', 'other'))",
    "ALTER TABLE voice_numbers ADD COLUMN forwarding_confirmed boolean NOT NULL DEFAULT false",
]

DOWN = [
    "ALTER TABLE voice_numbers DROP COLUMN forwarding_confirmed",
    "ALTER TABLE voice_numbers DROP COLUMN carrier",
    "ALTER TABLE voice_numbers DROP COLUMN client_public_number",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
