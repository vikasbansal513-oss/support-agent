"""Section 2 (Speed & Scaling): indexes for the Client Dashboard tabs that were previously
unindexed scans.

- messages.inbound_event_id had no index at all, even though it is an FK. app/api/
  actions.py's Review-tab query runs a correlated subquery against `messages` filtered on
  this column once per returned row (20 rows per page), which forced a full scan of the
  tenant's whole `messages` table 20 times over on every Review tab load.
- handoffs had no index beyond a narrow partial unique index (open/claimed only, no
  would_handoff/priority/sla_due_at columns). Both the Handoffs tab's own list query
  (app/api/handoffs.py) and the Dashboard stats query's handed_off/open_handoffs counts
  (app/api/dashboard.py) scanned the entire table every time.
- agent_runs had no index beyond its unique constraints. The Dashboard stats query's
  "handled today" count filters agent_runs by status + started_at on every load with no
  supporting index.
- agent_actions.tool_name (used by the Approvals tab's tool-name filter in
  app/api/approvals.py) had no supporting index either; ix_actions_status from migration
  0007 only helps once a status filter narrows things down first.

NOTE for whoever runs this against the production database: these are plain
`CREATE INDEX` statements (matching every other index-only migration in this repo, e.g.
0035_retention_scan_indexes.py), which takes a lock for the duration of the build. On a
small/empty table that's instant; if `messages`, `handoffs` or `agent_actions` have already
grown large by the time this runs, consider running the equivalent
`CREATE INDEX CONCURRENTLY` statements by hand outside a transaction first, then marking
this migration as applied, instead of running this file as-is.
"""

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

UP = [
    "CREATE INDEX ix_messages_inbound_event ON messages (tenant_id, inbound_event_id)"
    " WHERE inbound_event_id IS NOT NULL",
    "CREATE INDEX ix_handoffs_queue ON handoffs"
    " (tenant_id, would_handoff, status, priority, sla_due_at, created_at)",
    "CREATE INDEX ix_handoffs_tenant_wouldhandoff_created ON handoffs"
    " (tenant_id, would_handoff, created_at)",
    "CREATE INDEX ix_agent_runs_tenant_status_started ON agent_runs"
    " (tenant_id, status, started_at)",
    "CREATE INDEX ix_actions_tool_name ON agent_actions (tenant_id, tool_name, created_at DESC)"
    " WHERE tool_name IS NOT NULL",
]

DOWN = [
    "DROP INDEX ix_actions_tool_name",
    "DROP INDEX ix_agent_runs_tenant_status_started",
    "DROP INDEX ix_handoffs_tenant_wouldhandoff_created",
    "DROP INDEX ix_handoffs_queue",
    "DROP INDEX ix_messages_inbound_event",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
