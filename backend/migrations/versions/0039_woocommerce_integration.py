"""integrations: add 'woocommerce' as a store platform kind (one of Shopify / WooCommerce per brand)"""
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

_FIND_CHECK = """DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT conname FROM pg_constraint
    WHERE contype = 'c' AND conrelid = 'integrations'::regclass
      AND pg_get_constraintdef(oid) ~ 'kind = ANY'
  LOOP
    EXECUTE format('ALTER TABLE integrations DROP CONSTRAINT %I', r.conname);
  END LOOP;
END $$"""

_OLD_KINDS = (
    "'shopify','gupshup','gmail','shiprocket','delhivery',"
    "'bluedart','dtdc','ecom_express','xpressbees','shadowfax'"
)
_NEW_KINDS = (
    "'shopify','woocommerce','gupshup','gmail','shiprocket','delhivery',"
    "'bluedart','dtdc','ecom_express','xpressbees','shadowfax'"
)

UP = [
    _FIND_CHECK,
    f"ALTER TABLE integrations ADD CONSTRAINT integrations_kind_check CHECK (kind IN ({_NEW_KINDS}))",
]

DOWN = [
    "DELETE FROM integrations WHERE kind = 'woocommerce'",
    _FIND_CHECK,
    f"ALTER TABLE integrations ADD CONSTRAINT integrations_kind_check CHECK (kind IN ({_OLD_KINDS}))",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
