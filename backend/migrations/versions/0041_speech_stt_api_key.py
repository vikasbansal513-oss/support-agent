"""speech: add stt_api_key_enc to platform_speech_settings, so STT and TTS can use
different provider API keys (e.g. Sarvam for STT, Google for TTS) instead of sharing
one key - needed now that stt_provider and tts_provider can genuinely differ.
"""

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

UP = [
    # Nullable, backward compatible: when this is NULL (every existing row, and any
    # admin save that doesn't set a separate STT key), app.speech.runtime.load_effective
    # falls back to api_key_enc for STT too - identical behaviour to before this
    # migration (STT and TTS sharing one key) until an admin explicitly sets a
    # different STT key from the dashboard.
    "ALTER TABLE platform_speech_settings ADD COLUMN stt_api_key_enc text",
]

DOWN = [
    "ALTER TABLE platform_speech_settings DROP COLUMN stt_api_key_enc",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
