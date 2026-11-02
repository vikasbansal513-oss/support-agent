"""Fix: admin Control Tower-wide provider monitoring was silently empty. Every
/admin/monitoring/providers (and the new provider_api_failure/integration_latency_high
alert checks in core/alerts.py) reads external_calls under a super_admin session with no
tenant_id set, relying on external_calls' RLS super_admin_read policy to see rows at all
-- it never had one (unlike agent_runs/handoffs/llm_calls, which got theirs in 0005/0007).
external_calls holds provider/http_status/latency only, no customer content, so this is
safe to widen the same way those tables already were.

Deliberately NOT touched here: agent_actions, shadow_verdicts, answer_reports. Those hold
customer-facing reply text and review notes, and tests/rls/test_p5_tables.py::
test_tenant_isolation_and_super_admin_read already asserts, by design, that a super_admin
session with no tenant_id cannot read agent_actions. So platform-wide (all-clients, no
tenant filter) agent-performance/accuracy numbers are correctness-blind by the same
existing design -- see DECISIONS.md 2026-11-02 and CHANGE_REPORT; app/api/admin_monitoring.py
now passes the requested tenant_id into the RLS session (not just as a SQL filter) for the
per-client endpoints, which is the real fix for the client-wise monitoring this round's
spec actually asked for."""

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

UP = ['CREATE POLICY super_admin_read ON "external_calls" FOR SELECT USING (app_is_super_admin())']

DOWN = ['DROP POLICY super_admin_read ON "external_calls"']


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
