"""agent_runs, agent_actions, handoffs, llm_model_prices, llm_calls, answer_reports, shadow_verdicts"""
from alembic import op

from app.core.tenancy import tenant_rls_statements

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

UP = [
    """CREATE TABLE agent_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  inbound_event_id uuid NOT NULL, conversation_id uuid NOT NULL,
  graph_version text NOT NULL, prompt_versions jsonb NOT NULL DEFAULT '{}', policy_version integer,
  mode text NOT NULL CHECK (mode IN ('shadow','draft','auto')),
  decision jsonb, trace jsonb NOT NULL DEFAULT '[]',
  status text NOT NULL DEFAULT 'running' CHECK (status IN ('running','done','failed','skipped')),
  error_id text, started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
  UNIQUE (tenant_id, id), UNIQUE (tenant_id, inbound_event_id),
  FOREIGN KEY (tenant_id, inbound_event_id) REFERENCES inbound_events (tenant_id, id),
  FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations (tenant_id, id)
)""",
    *tenant_rls_statements("agent_runs", super_admin_read=True),
    """CREATE TABLE agent_actions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  run_id uuid, conversation_id uuid NOT NULL,
  action_type text NOT NULL CHECK (action_type IN ('reply_customer','handoff_notify','tool_call')),
  idempotency_key text NOT NULL,
  status text NOT NULL CHECK (status IN
    ('claimed','shadow','draft','sent','failed','needs_review','rejected','suppressed')),
  recipient text, body_text text, tool_name text, tool_args jsonb, tool_result jsonb,
  external_ref text, attempts integer NOT NULL DEFAULT 0,
  claimed_at timestamptz NOT NULL DEFAULT now(), sent_at timestamptz,
  error text, decided_by uuid, decided_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id), UNIQUE (tenant_id, idempotency_key),
  FOREIGN KEY (tenant_id, run_id) REFERENCES agent_runs (tenant_id, id),
  FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations (tenant_id, id),
  CHECK (action_type <> 'tool_call' OR tool_name IS NOT NULL),
  CHECK (action_type <> 'reply_customer' OR recipient IS NOT NULL)
)""",
    "CREATE INDEX ix_actions_status ON agent_actions (tenant_id, status, created_at DESC)",
    *tenant_rls_statements("agent_actions"),
    """CREATE TABLE handoffs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  conversation_id uuid NOT NULL, run_id uuid,
  reason_code text NOT NULL, priority text NOT NULL DEFAULT 'normal'
    CHECK (priority IN ('normal','urgent')),
  summary jsonb NOT NULL, would_handoff boolean NOT NULL DEFAULT false,
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','claimed','resolved')),
  assigned_user_id uuid, sla_due_at timestamptz, alert_count integer NOT NULL DEFAULT 0,
  last_alert_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz, resolved_by uuid,
  UNIQUE (tenant_id, id),
  FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations (tenant_id, id),
  FOREIGN KEY (tenant_id, run_id) REFERENCES agent_runs (tenant_id, id)
)""",
    "CREATE UNIQUE INDEX ux_handoff_one_open ON handoffs (tenant_id, conversation_id)"
    " WHERE status IN ('open','claimed')",
    *tenant_rls_statements("handoffs", super_admin_read=True),
    """CREATE TABLE llm_model_prices (
  provider text NOT NULL, model text NOT NULL,
  input_per_million numeric(18,6) NOT NULL CHECK (input_per_million >= 0),
  output_per_million numeric(18,6) NOT NULL CHECK (output_per_million >= 0),
  PRIMARY KEY (provider, model)
)""",
    "REVOKE INSERT, UPDATE, DELETE ON llm_model_prices FROM support_app_runtime",
    """CREATE TABLE llm_calls (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  run_id uuid, purpose text NOT NULL, provider text NOT NULL, model text NOT NULL,
  prompt_version text,
  input_tokens integer NOT NULL DEFAULT 0, output_tokens integer NOT NULL DEFAULT 0,
  est_cost_micro bigint,
  latency_ms integer NOT NULL,
  outcome text NOT NULL CHECK (outcome IN ('ok','invalid_json','timeout','error')),
  created_at timestamptz NOT NULL DEFAULT now()
)""",
    "CREATE INDEX ix_llm_calls_time ON llm_calls (created_at DESC)",
    *tenant_rls_statements("llm_calls", super_admin_read=True),
    """CREATE TABLE answer_reports (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  message_id uuid NOT NULL, action_id uuid, reported_by uuid NOT NULL, reason text,
  status text NOT NULL DEFAULT 'open', handoff_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant_id, message_id) REFERENCES messages (tenant_id, id),
  FOREIGN KEY (tenant_id, action_id) REFERENCES agent_actions (tenant_id, id),
  FOREIGN KEY (tenant_id, handoff_id) REFERENCES handoffs (tenant_id, id)
)""",
    *tenant_rls_statements("answer_reports"),
    """CREATE TABLE shadow_verdicts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  action_id uuid NOT NULL, verdict text NOT NULL CHECK (verdict IN ('correct','wrong')), note text,
  by_user uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, action_id),
  FOREIGN KEY (tenant_id, action_id) REFERENCES agent_actions (tenant_id, id)
)""",
    *tenant_rls_statements("shadow_verdicts"),
]

DOWN = [
    "DROP TABLE shadow_verdicts",
    "DROP TABLE answer_reports",
    "DROP TABLE llm_calls",
    "DROP TABLE llm_model_prices",
    "DROP TABLE handoffs",
    "DROP TABLE agent_actions",
    "DROP TABLE agent_runs",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
