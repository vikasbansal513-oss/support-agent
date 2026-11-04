"""voice_calls: explicit AI-disclosure consent capture fields (Decision Framework 17.1)"""
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

UP = [
    "ALTER TABLE voice_calls ADD COLUMN consent_response text",
    # NULL means "not asked/answered yet" - safe default for existing rows, and the value
    # _turn() checks to decide whether to run the consent gate before anything else.
    "ALTER TABLE voice_calls ADD CONSTRAINT voice_calls_consent_response_check"
    " CHECK (consent_response IS NULL OR consent_response IN ('yes','no'))",
    "ALTER TABLE voice_calls ADD COLUMN consent_responded_at timestamptz",
]

DOWN = [
    "ALTER TABLE voice_calls DROP COLUMN consent_responded_at",
    "ALTER TABLE voice_calls DROP CONSTRAINT voice_calls_consent_response_check",
    "ALTER TABLE voice_calls DROP COLUMN consent_response",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
