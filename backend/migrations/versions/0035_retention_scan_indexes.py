"""Section 2 (Speed & Scaling): indexes for retention/cleanup sweeps that were previously
unindexed scans - inbound_events, jobs and rate_limits retention deletes
(app/privacy/service.py's periodic cleanup) and the tenant_api rate-limit bucket that now
writes to rate_limits on every request after login (app/auth/ratelimit.py)."""

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

UP = [
    "CREATE INDEX ix_inbound_events_tenant_received ON inbound_events (tenant_id, received_at)",
    "CREATE INDEX ix_jobs_tenant_done_finished ON jobs (tenant_id, finished_at)"
    " WHERE status = 'done'",
    "CREATE INDEX ix_rate_limits_window ON rate_limits (window_start)",
]

DOWN = [
    "DROP INDEX ix_rate_limits_window",
    "DROP INDEX ix_jobs_tenant_done_finished",
    "DROP INDEX ix_inbound_events_tenant_received",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
