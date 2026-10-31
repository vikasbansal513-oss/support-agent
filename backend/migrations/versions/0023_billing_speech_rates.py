"""billing: STT/TTS provider rates (settings only, usage not yet metered)"""
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

UP = [
    """CREATE TABLE billing_speech_rates (
  provider text NOT NULL CHECK (provider IN
    ('sarvam_stt', 'google_stt', 'sarvam_tts', 'google_tts')),
  unit text NOT NULL CHECK (unit ~ '^[a-z0-9_]{1,40}$'),
  rate_inr numeric(18,6) NOT NULL DEFAULT 0 CHECK (rate_inr >= 0),
  active boolean NOT NULL DEFAULT true,
  updated_by uuid,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (provider)
)""",
    'ALTER TABLE "billing_speech_rates" ENABLE ROW LEVEL SECURITY',
    'ALTER TABLE "billing_speech_rates" FORCE ROW LEVEL SECURITY',
    'CREATE POLICY super_admin_only ON "billing_speech_rates"'
    " USING (app_is_super_admin()) WITH CHECK (app_is_super_admin())",
]

DOWN = [
    "DROP TABLE billing_speech_rates",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
