"""voice quality: provider per number, latency counters, call feedback and summary status"""
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

UP = [
    "ALTER TABLE voice_numbers ADD COLUMN provider text NOT NULL DEFAULT 'generic'"
    " CHECK (provider ~ '^[a-z0-9_]{1,40}$')",
    "ALTER TABLE voice_calls ADD COLUMN latency_total_ms bigint NOT NULL DEFAULT 0"
    " CHECK (latency_total_ms >= 0)",
    "ALTER TABLE voice_calls ADD COLUMN latency_max_ms integer NOT NULL DEFAULT 0"
    " CHECK (latency_max_ms >= 0)",
    "ALTER TABLE voice_calls ADD COLUMN slow_turns integer NOT NULL DEFAULT 0"
    " CHECK (slow_turns >= 0)",
    "ALTER TABLE voice_calls ADD COLUMN order_ref text",
    "ALTER TABLE voice_calls ADD COLUMN summary_status text"
    " CHECK (summary_status IN ('sent','skipped','failed'))",
    "ALTER TABLE voice_calls ADD COLUMN feedback text CHECK (feedback IN ('good','bad'))",
    "ALTER TABLE voice_calls ADD COLUMN feedback_note text CHECK (char_length(feedback_note) <= 300)",
    "ALTER TABLE voice_calls ADD COLUMN feedback_at timestamptz",
]

DOWN = [
    "ALTER TABLE voice_calls DROP COLUMN feedback_at",
    "ALTER TABLE voice_calls DROP COLUMN feedback_note",
    "ALTER TABLE voice_calls DROP COLUMN feedback",
    "ALTER TABLE voice_calls DROP COLUMN summary_status",
    "ALTER TABLE voice_calls DROP COLUMN order_ref",
    "ALTER TABLE voice_calls DROP COLUMN slow_turns",
    "ALTER TABLE voice_calls DROP COLUMN latency_max_ms",
    "ALTER TABLE voice_calls DROP COLUMN latency_total_ms",
    "ALTER TABLE voice_numbers DROP COLUMN provider",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
