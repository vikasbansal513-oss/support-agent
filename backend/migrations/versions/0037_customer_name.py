"""customers.name - best-effort display name (WhatsApp profile name, email From header
name, or Shopify order customer name), fill-in-only like phone/email identity linking.
Never a required field; dashboard falls back to the masked phone/email when absent."""
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

UP = [
    "ALTER TABLE customers ADD COLUMN name text",
]

DOWN = [
    "ALTER TABLE customers DROP COLUMN name",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
