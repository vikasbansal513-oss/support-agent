"""Control Tower alerts v2: Critical/High/Medium/Low severity (adds 'high' alongside the
existing info/warning/critical -- nothing already stored changes meaning), New/Acknowledged/
Resolved status (adds 'acknowledged'), plus columns so an alert can say which client, agent,
service and provider it is about, its error id / root cause, and how it was resolved. New
monitoring_thresholds for the new checks added in core/alerts.py this round (per-agent
accuracy already existed; this adds per-run-failure-rate and per-provider-error-rate)."""

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

UP = [
    "ALTER TABLE alerts DROP CONSTRAINT alerts_severity_check",
    "ALTER TABLE alerts ADD CONSTRAINT alerts_severity_check"
    " CHECK (severity IN ('info','warning','high','critical'))",
    "ALTER TABLE alerts DROP CONSTRAINT alerts_status_check",
    "ALTER TABLE alerts ADD CONSTRAINT alerts_status_check"
    " CHECK (status IN ('open','acknowledged','resolved'))",
    "ALTER TABLE alerts ADD COLUMN service text NOT NULL DEFAULT '' CHECK (length(service) <= 60)",
    "ALTER TABLE alerts ADD COLUMN affected_agent text CHECK (length(affected_agent) <= 120)",
    "ALTER TABLE alerts ADD COLUMN error_id text CHECK (length(error_id) <= 60)",
    "ALTER TABLE alerts ADD COLUMN root_cause text CHECK (length(root_cause) <= 500)",
    "ALTER TABLE alerts ADD COLUMN acknowledged_at timestamptz",
    "ALTER TABLE alerts ADD COLUMN acknowledged_by uuid",
    "ALTER TABLE alerts ADD COLUMN resolution_notes text CHECK (length(resolution_notes) <= 500)",
    "CREATE INDEX ix_alerts_severity_status ON alerts (severity, status, created_at DESC)",
    "SET LOCAL app.is_super_admin = 'on'",
    "INSERT INTO monitoring_thresholds (key, value) VALUES"
    " ('agent_run_failure_window_count', 5),"
    " ('provider_error_rate_percent', 30),"
    " ('provider_min_calls_for_alert', 5)",
]

DOWN = [
    "DELETE FROM monitoring_thresholds WHERE key IN"
    " ('agent_run_failure_window_count', 'provider_error_rate_percent',"
    " 'provider_min_calls_for_alert')",
    "DROP INDEX ix_alerts_severity_status",
    "ALTER TABLE alerts DROP COLUMN resolution_notes",
    "ALTER TABLE alerts DROP COLUMN acknowledged_by",
    "ALTER TABLE alerts DROP COLUMN acknowledged_at",
    "ALTER TABLE alerts DROP COLUMN root_cause",
    "ALTER TABLE alerts DROP COLUMN error_id",
    "ALTER TABLE alerts DROP COLUMN affected_agent",
    "ALTER TABLE alerts DROP COLUMN service",
    "ALTER TABLE alerts DROP CONSTRAINT alerts_status_check",
    "ALTER TABLE alerts ADD CONSTRAINT alerts_status_check CHECK (status IN ('open','resolved'))",
    "ALTER TABLE alerts DROP CONSTRAINT alerts_severity_check",
    "ALTER TABLE alerts ADD CONSTRAINT alerts_severity_check"
    " CHECK (severity IN ('info','warning','critical'))",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
