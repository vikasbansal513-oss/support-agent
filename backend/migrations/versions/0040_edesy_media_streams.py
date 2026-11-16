"""voice: diy_edesy telephony_mode + platform_speech_settings (admin-chosen STT/TTS provider+key)"""

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None

UP = [
    "ALTER TABLE voice_numbers DROP CONSTRAINT voice_numbers_telephony_mode_check",
    "ALTER TABLE voice_numbers ADD CONSTRAINT voice_numbers_telephony_mode_check"
    " CHECK (telephony_mode IN ('ready_made', 'diy_plivo', 'diy_edesy'))",
    """CREATE TABLE platform_speech_settings (
  id smallint PRIMARY KEY CHECK (id = 1),
  stt_provider text NOT NULL CHECK (stt_provider IN ('google', 'sarvam')),
  tts_provider text NOT NULL CHECK (tts_provider IN ('google', 'sarvam')),
  sample_rate_hz smallint NOT NULL DEFAULT 16000 CHECK (sample_rate_hz IN (8000, 16000)),
  api_key_enc text,
  updated_by uuid,
  updated_at timestamptz NOT NULL DEFAULT now()
)""",
    'ALTER TABLE "platform_speech_settings" ENABLE ROW LEVEL SECURITY',
    'ALTER TABLE "platform_speech_settings" FORCE ROW LEVEL SECURITY',
    "CREATE POLICY super_admin_only ON platform_speech_settings"
    " USING (app_is_super_admin()) WITH CHECK (app_is_super_admin())",
]

DOWN = [
    "DROP TABLE platform_speech_settings",
    "ALTER TABLE voice_numbers DROP CONSTRAINT voice_numbers_telephony_mode_check",
    "ALTER TABLE voice_numbers ADD CONSTRAINT voice_numbers_telephony_mode_check"
    " CHECK (telephony_mode IN ('ready_made', 'diy_plivo'))",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
