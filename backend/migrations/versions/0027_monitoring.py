"""Control Tower monitoring: page_perf_events (Web Vitals), monitoring_thresholds, partial verdict"""
from alembic import op

from app.core.tenancy import tenant_rls_statements

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def _admin_only(table: str) -> list[str]:
    return [
        f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY',
        f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY',
        f'CREATE POLICY super_admin_only ON "{table}"'
        " USING (app_is_super_admin()) WITH CHECK (app_is_super_admin())",
    ]


UP = [
    """CREATE TABLE page_perf_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  route text NOT NULL CHECK (length(route) BETWEEN 1 AND 120),
  ttfb_ms integer, dom_content_loaded_ms integer, load_ms integer,
  lcp_ms integer, inp_ms integer, cls numeric(6,4),
  api_duration_ms integer, failed_api boolean NOT NULL DEFAULT false,
  user_agent text CHECK (length(user_agent) <= 300),
  created_at timestamptz NOT NULL DEFAULT now()
)""",
    "CREATE INDEX ix_page_perf_tenant_route_time ON page_perf_events (tenant_id, route, created_at DESC)",
    "CREATE INDEX ix_page_perf_time ON page_perf_events (created_at DESC)",
    *tenant_rls_statements("page_perf_events", super_admin_read=True),
    """CREATE TABLE monitoring_thresholds (
  key text PRIMARY KEY CHECK (length(key) BETWEEN 1 AND 60),
  value numeric NOT NULL,
  updated_by uuid, updated_at timestamptz NOT NULL DEFAULT now()
)""",
    *_admin_only("monitoring_thresholds"),
    # FORCE RLS applies even to the migration/owner role; without this the seed INSERT
    # below is rejected (same issue fixed for billing_cost_categories in 0025).
    "SET LOCAL app.is_super_admin = 'on'",
    "INSERT INTO monitoring_thresholds (key, value) VALUES"
    " ('agent_accuracy_min_percent', 80),"
    " ('agent_accuracy_min_evaluated', 10),"
    " ('page_load_slow_ms', 4000),"
    " ('page_load_min_samples', 5),"
    " ('api_latency_p95_ms', 3000),"
    " ('queue_backlog_jobs', 50),"
    " ('failed_jobs_window_count', 5),"
    " ('db_connections_warn_percent', 80)",
    # verdict was correct|wrong only; add 'partial' so reviewers can mark a reply partially right
    # without it being invisible to accuracy reporting (see DECISIONS.md 2026-11-02)
    "ALTER TABLE shadow_verdicts DROP CONSTRAINT shadow_verdicts_verdict_check",
    "ALTER TABLE shadow_verdicts ADD CONSTRAINT shadow_verdicts_verdict_check"
    " CHECK (verdict IN ('correct','wrong','partial'))",
]

DOWN = [
    "ALTER TABLE shadow_verdicts DROP CONSTRAINT shadow_verdicts_verdict_check",
    "ALTER TABLE shadow_verdicts ADD CONSTRAINT shadow_verdicts_verdict_check"
    " CHECK (verdict IN ('correct','wrong'))",
    "DROP TABLE monitoring_thresholds",
    "DROP TABLE page_perf_events",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
