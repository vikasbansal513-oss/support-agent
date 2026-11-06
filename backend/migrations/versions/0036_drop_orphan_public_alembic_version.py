"""Defensive cleanup: an earlier deploy run (before this project's alembic_meta
schema-separation design was consistently applied end-to-end) left a stray
`public.alembic_version` table behind (a single row, version_num='0034') on at
least one real environment. migrations/env.py has always pointed Alembic's own
version-tracking table at the alembic_meta schema (see migrations/env.py's
VERSION_SCHEMA / version_table_schema, and DECISIONS.md's P1 entry), so that
table is pure orphaned debris - Alembic itself never reads or writes it, it's
alembic_meta.alembic_version that's authoritative.

It matters because app/core/db.py's check_rls_safety() (run on every GET
/ready) flags any public-schema table that has no tenant_id column and isn't
on GLOBAL_TABLE_ALLOWLIST - `public.alembic_version` trips that check, so
/ready returns 503 forever on an affected environment even though the app and
the real migration state are both fine. DROP IF EXISTS makes this a no-op on
any environment that never had the stray table (a fresh DB, or one already
fixed by hand), so it's safe to run everywhere.
"""

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.alembic_version")


def downgrade() -> None:
    # Nothing to restore - the table this removes was never part of the real
    # schema (Alembic's own version table lives in alembic_meta, not public),
    # so there is no meaningful "down" for this cleanup.
    pass
