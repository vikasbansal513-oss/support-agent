"""privacy_requests: structured fields for correction (kind=correct) requests"""
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

UP = [
    "ALTER TABLE privacy_requests ADD COLUMN correction_field text",
    "ALTER TABLE privacy_requests ADD COLUMN correction_value text",
    # Safe for existing rows: correction_field is NULL on every row created before this
    # change, and NULL trivially satisfies the IN-list check below.
    "ALTER TABLE privacy_requests ADD CONSTRAINT privacy_requests_correction_field_check"
    " CHECK (correction_field IS NULL OR correction_field IN ('phone','email'))",
]

DOWN = [
    "ALTER TABLE privacy_requests DROP CONSTRAINT privacy_requests_correction_field_check",
    "ALTER TABLE privacy_requests DROP COLUMN correction_value",
    "ALTER TABLE privacy_requests DROP COLUMN correction_field",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
