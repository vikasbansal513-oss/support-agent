"""platform_llm_settings: admin-chosen LLM provider, model and encrypted key"""
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

UP = [
    """CREATE TABLE platform_llm_settings (
  id smallint PRIMARY KEY CHECK (id = 1),
  provider text NOT NULL
    CHECK (provider IN ('anthropic','openai','groq','openrouter','openai_compatible')),
  model text NOT NULL CHECK (length(model) BETWEEN 1 AND 200),
  base_url text NOT NULL DEFAULT '' CHECK (length(base_url) <= 300),
  api_key_enc text,
  updated_by uuid,
  updated_at timestamptz NOT NULL DEFAULT now()
)""",
    'ALTER TABLE "platform_llm_settings" ENABLE ROW LEVEL SECURITY',
    'ALTER TABLE "platform_llm_settings" FORCE ROW LEVEL SECURITY',
    "CREATE POLICY super_admin_only ON platform_llm_settings"
    " USING (app_is_super_admin()) WITH CHECK (app_is_super_admin())",
]

DOWN = [
    "DROP TABLE platform_llm_settings",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
