-- Support Agent V0 schema. Executable reference; Claude Code turns this into Alembic migrations 0001-0009 (TDD 4.3).
-- Run as the migration OWNER role (never as the runtime role).
\set ON_ERROR_STOP on

-- ===== 0001 foundation =====
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'support_app_runtime') THEN
    CREATE ROLE support_app_runtime NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'support_app_runtime' AND (rolsuper OR rolbypassrls)) THEN
    RAISE EXCEPTION 'runtime role must not be superuser/BYPASSRLS';
  END IF;
END $$;
GRANT USAGE ON SCHEMA public TO support_app_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO support_app_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO support_app_runtime;

CREATE FUNCTION app_current_tenant() RETURNS uuid LANGUAGE sql STABLE AS
$$ SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid $$;
CREATE FUNCTION app_current_user() RETURNS uuid LANGUAGE sql STABLE AS
$$ SELECT NULLIF(current_setting('app.user_id', true), '')::uuid $$;
CREATE FUNCTION app_is_super_admin() RETURNS boolean LANGUAGE sql STABLE AS
$$ SELECT COALESCE(NULLIF(current_setting('app.is_super_admin', true), ''), 'off') = 'on' $$;
GRANT EXECUTE ON FUNCTION app_current_tenant(), app_current_user(), app_is_super_admin() TO support_app_runtime;

CREATE TABLE audit_log (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  occurred_at timestamptz NOT NULL DEFAULT now(),
  tenant_id uuid DEFAULT app_current_tenant(),
  actor_user_id uuid, actor_role text,
  action text NOT NULL CHECK (length(btrim(action)) > 0),
  entity_type text, entity_id text, old_value jsonb, new_value jsonb, reason text, source_ref text, run_id text
);
CREATE INDEX ix_audit_log_tenant_time ON audit_log (tenant_id, occurred_at DESC);
CREATE INDEX ix_audit_log_action ON audit_log (action, occurred_at DESC);
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log FORCE ROW LEVEL SECURITY;
CREATE POLICY audit_select ON audit_log FOR SELECT USING (tenant_id = app_current_tenant() OR app_is_super_admin());
CREATE POLICY audit_insert ON audit_log FOR INSERT
  WITH CHECK (tenant_id IS NOT DISTINCT FROM app_current_tenant() OR app_is_super_admin());
CREATE FUNCTION append_only() RETURNS trigger LANGUAGE plpgsql AS
$$ BEGIN RAISE EXCEPTION '% is append-only: UPDATE/DELETE/TRUNCATE not allowed', TG_TABLE_NAME; END $$;
CREATE TRIGGER audit_log_no_row_mutation BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW EXECUTE FUNCTION append_only();
CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log FOR EACH STATEMENT EXECUTE FUNCTION append_only();
REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM support_app_runtime;

-- ===== 0002 tenants, users, auth (GST 0003/0017 shape) =====
CREATE TABLE tenants (
  id uuid PRIMARY KEY,
  name text NOT NULL CHECK (length(btrim(name)) > 0),
  slug text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,40}$'),
  status text NOT NULL DEFAULT 'pending_approval' CHECK (status IN ('pending_approval','active','paused','suspended')),
  agent_paused boolean NOT NULL DEFAULT false,
  mode text NOT NULL DEFAULT 'shadow' CHECK (mode IN ('shadow','draft','auto')),
  max_tool_risk text NOT NULL DEFAULT 'low' CHECK (max_tool_risk IN ('low')),
  plan text NOT NULL DEFAULT 'pilot',
  included_conversations integer NOT NULL DEFAULT 500 CHECK (included_conversations >= 0),
  usage_override_limit integer CHECK (usage_override_limit >= 0),
  approved_by uuid, approved_at timestamptz, notes text,
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenants_rls ON tenants USING (id = app_current_tenant() OR app_is_super_admin())
  WITH CHECK (id = app_current_tenant() OR app_is_super_admin());

-- Global tables (NO tenant_id, NO RLS): only auth/service code touches them. Allowlist is enforced by a test (see rls_tests.sql).
CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text NOT NULL CHECK (email = lower(email) AND length(email) <= 254),
  name text NOT NULL CHECK (length(btrim(name)) > 0),
  password_hash text,
  email_verified_at timestamptz,
  failed_attempts integer NOT NULL DEFAULT 0 CHECK (failed_attempts >= 0),
  locked_until timestamptz,
  is_active boolean NOT NULL DEFAULT true,
  password_changed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_users_email ON users (email);
CREATE TABLE platform_admins (user_id uuid PRIMARY KEY REFERENCES users(id), created_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE memberships (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  user_id uuid NOT NULL REFERENCES users(id),
  role text NOT NULL CHECK (role IN ('owner','agent')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, user_id), UNIQUE (user_id)  -- V0: one user = one brand
);
ALTER TABLE memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE memberships FORCE ROW LEVEL SECURITY;
CREATE POLICY memberships_rls ON memberships
  USING (tenant_id = app_current_tenant() OR user_id = app_current_user() OR app_is_super_admin())
  WITH CHECK (tenant_id = app_current_tenant() OR app_is_super_admin());

CREATE TABLE email_tokens (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id),
  purpose text NOT NULL CHECK (purpose IN ('verify','reset')),
  token_hash text NOT NULL UNIQUE, expires_at timestamptz NOT NULL, used_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_email_tokens_user ON email_tokens (user_id, purpose, created_at DESC);
CREATE TABLE refresh_tokens (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id),
  realm text NOT NULL DEFAULT 'brand' CHECK (realm IN ('brand','admin')),
  family_id uuid NOT NULL, token_hash text NOT NULL UNIQUE, expires_at timestamptz NOT NULL,
  revoked_at timestamptz, replaced_by uuid, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_refresh_family ON refresh_tokens (family_id);
CREATE INDEX ix_refresh_user ON refresh_tokens (user_id);
CREATE TABLE rate_limits (
  bucket text NOT NULL, window_start timestamptz NOT NULL, hits integer NOT NULL CHECK (hits >= 0),
  PRIMARY KEY (bucket, window_start)
);
CREATE TABLE user_mfa (
  user_id uuid PRIMARY KEY REFERENCES users(id),
  secret_enc text NOT NULL, confirmed_at timestamptz, last_step bigint,
  recovery_hashes text[] NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);

-- ===== 0003 integrations, settings, external calls =====
CREATE TABLE integrations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  kind text NOT NULL CHECK (kind IN ('shopify','gupshup','gmail','shiprocket','delhivery')),
  config jsonb NOT NULL DEFAULT '{}',
  secret_enc text,
  webhook_secret_hash text,
  status text NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','auth_error','disabled')),
  last_error_code text, last_checked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, kind)
);
-- Webhook tenant lookup without opening RLS: one row per WhatsApp number, no secrets.
CREATE TABLE wa_numbers (
  source_number text PRIMARY KEY, app_name text NOT NULL DEFAULT '',
  webhook_token_hash text NOT NULL UNIQUE CHECK (webhook_token_hash ~ '^[0-9a-f]{64}$'),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() UNIQUE REFERENCES tenants(id)
);
ALTER TABLE wa_numbers ENABLE ROW LEVEL SECURITY;
ALTER TABLE wa_numbers FORCE ROW LEVEL SECURITY;
CREATE POLICY wa_lookup ON wa_numbers FOR SELECT USING (true);  -- webhook has no tenant context yet; row holds only the sha256 of the webhook token (no secret)
CREATE POLICY wa_write_ins ON wa_numbers FOR INSERT WITH CHECK (tenant_id = app_current_tenant());
CREATE POLICY wa_write_upd ON wa_numbers FOR UPDATE USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
CREATE POLICY wa_write_del ON wa_numbers FOR DELETE USING (tenant_id = app_current_tenant());

CREATE TABLE tenant_settings (
  tenant_id uuid PRIMARY KEY DEFAULT app_current_tenant() REFERENCES tenants(id),
  notify_email_enabled boolean NOT NULL DEFAULT true, notify_email text,
  notify_whatsapp_enabled boolean NOT NULL DEFAULT false, alert_phone text,
  grievance_email text, timezone text NOT NULL DEFAULT 'Asia/Kolkata',
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- ===== 0004 policies =====
CREATE TABLE policies (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  version integer NOT NULL CHECK (version >= 1),
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','archived')),
  content jsonb NOT NULL, content_hash text NOT NULL,
  created_by uuid, created_at timestamptz NOT NULL DEFAULT now(),
  regression_run_id uuid, published_by uuid, published_at timestamptz,
  UNIQUE (tenant_id, version), UNIQUE (tenant_id, id),
  CHECK (status <> 'published' OR (published_by IS NOT NULL AND published_at IS NOT NULL AND regression_run_id IS NOT NULL))
);
CREATE UNIQUE INDEX ux_policies_one_published ON policies (tenant_id) WHERE status = 'published';

-- ===== 0005 customers, conversations, messages, inbound events, usage =====
CREATE TABLE customers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  phone_e164 text CHECK (phone_e164 ~ '^\+[1-9][0-9]{7,14}$'),
  email_lower text CHECK (email_lower = lower(email_lower)),
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (phone_e164 IS NOT NULL OR email_lower IS NOT NULL),
  UNIQUE (tenant_id, id)
);
CREATE UNIQUE INDEX ux_customers_phone ON customers (tenant_id, phone_e164) WHERE phone_e164 IS NOT NULL;
CREATE UNIQUE INDEX ux_customers_email ON customers (tenant_id, email_lower) WHERE email_lower IS NOT NULL;

CREATE TABLE conversations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  customer_id uuid NOT NULL,
  channel text NOT NULL CHECK (channel IN ('whatsapp','email')),
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','handoff','resolved','closed')),
  active_order_ref text, identity_attempts integer NOT NULL DEFAULT 0 CHECK (identity_attempts >= 0),
  language text CHECK (language IN ('hi','en','hinglish')),
  human_active boolean NOT NULL DEFAULT false,
  counted_for_usage boolean NOT NULL DEFAULT false,
  last_inbound_at timestamptz, last_message_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  FOREIGN KEY (tenant_id, customer_id) REFERENCES customers (tenant_id, id)
);
CREATE INDEX ix_conv_tenant_last ON conversations (tenant_id, last_message_at DESC);
CREATE UNIQUE INDEX ux_conv_one_open ON conversations (tenant_id, customer_id, channel) WHERE status IN ('open','handoff');

CREATE TABLE inbound_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  channel text NOT NULL CHECK (channel IN ('whatsapp','email')),
  external_id text NOT NULL,
  raw_payload jsonb NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  status text NOT NULL DEFAULT 'received' CHECK (status IN ('received','queued','processed','skipped','needs_review')),
  skip_reason text,
  UNIQUE (tenant_id, channel, external_id), UNIQUE (tenant_id, id)
);

CREATE TABLE messages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  conversation_id uuid NOT NULL,
  direction text NOT NULL CHECK (direction IN ('in','out')),
  sender text NOT NULL CHECK (sender IN ('customer','agent','human','system')),
  channel text NOT NULL CHECK (channel IN ('whatsapp','email')),
  content text NOT NULL, external_id text, inbound_event_id uuid, action_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations (tenant_id, id),
  FOREIGN KEY (tenant_id, inbound_event_id) REFERENCES inbound_events (tenant_id, id)
);
CREATE INDEX ix_messages_conv ON messages (tenant_id, conversation_id, created_at);

CREATE TABLE usage_counters (
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  period_start date NOT NULL, conversation_count integer NOT NULL DEFAULT 0 CHECK (conversation_count >= 0),
  PRIMARY KEY (tenant_id, period_start)
);

-- ===== 0006 jobs, notifications, heartbeats =====
CREATE TABLE jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  kind text NOT NULL, queue text NOT NULL DEFAULT 'default' CHECK (queue IN ('default','large')),
  payload jsonb NOT NULL DEFAULT '{}', idempotency_key text,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','deferred','processing','done','failed','needs_review')),
  attempts integer NOT NULL DEFAULT 0, max_attempts integer NOT NULL DEFAULT 3,
  run_after timestamptz NOT NULL DEFAULT now(),
  lease_owner text, lease_expires_at timestamptz, started_at timestamptz, finished_at timestamptz,
  duration_ms bigint NOT NULL DEFAULT 0, result jsonb,
  error_code text, error_message text, error_id text, created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (status <> 'processing' OR (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL))
);
CREATE UNIQUE INDEX ux_jobs_idem ON jobs (tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX ix_jobs_claim ON jobs (status, run_after) WHERE status IN ('queued','deferred');
CREATE TABLE notifications (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  audience text NOT NULL DEFAULT 'admin', title text NOT NULL, body text, link text, read_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE heartbeats (name text PRIMARY KEY, last_seen_at timestamptz NOT NULL DEFAULT now());

-- ===== 0007 agent =====
CREATE TABLE agent_runs (
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
);
CREATE TABLE agent_actions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  run_id uuid, conversation_id uuid NOT NULL,
  action_type text NOT NULL CHECK (action_type IN ('reply_customer','handoff_notify','tool_call')),
  idempotency_key text NOT NULL,
  status text NOT NULL CHECK (status IN ('claimed','shadow','draft','sent','failed','needs_review','rejected','suppressed')),
  recipient text, body_text text, tool_name text, tool_args jsonb, tool_result jsonb, external_ref text,
  attempts integer NOT NULL DEFAULT 0, claimed_at timestamptz NOT NULL DEFAULT now(), sent_at timestamptz,
  error text, decided_by uuid, decided_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id), UNIQUE (tenant_id, idempotency_key),
  FOREIGN KEY (tenant_id, run_id) REFERENCES agent_runs (tenant_id, id),
  FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations (tenant_id, id),
  CHECK (action_type <> 'tool_call' OR tool_name IS NOT NULL),
  CHECK (action_type <> 'reply_customer' OR recipient IS NOT NULL)
);
CREATE INDEX ix_actions_status ON agent_actions (tenant_id, status, created_at DESC);
CREATE TABLE external_calls (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  run_id uuid, provider text NOT NULL, operation text NOT NULL, request_summary jsonb,
  http_status integer, response_body_redacted text CHECK (length(response_body_redacted) <= 2048),
  error_class text, latency_ms integer, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_extcalls_tenant_time ON external_calls (tenant_id, created_at DESC);
CREATE TABLE handoffs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  conversation_id uuid NOT NULL, run_id uuid,
  reason_code text NOT NULL, priority text NOT NULL DEFAULT 'normal' CHECK (priority IN ('normal','urgent')),
  summary jsonb NOT NULL, would_handoff boolean NOT NULL DEFAULT false,
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','claimed','resolved')),
  assigned_user_id uuid, sla_due_at timestamptz, alert_count integer NOT NULL DEFAULT 0, last_alert_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(), resolved_at timestamptz, resolved_by uuid,
  UNIQUE (tenant_id, id),
  FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations (tenant_id, id),
  FOREIGN KEY (tenant_id, run_id) REFERENCES agent_runs (tenant_id, id)
);
CREATE UNIQUE INDEX ux_handoff_one_open ON handoffs (tenant_id, conversation_id) WHERE status IN ('open','claimed');
CREATE TABLE llm_model_prices (
  provider text NOT NULL, model text NOT NULL,
  input_per_million numeric(18,6) NOT NULL CHECK (input_per_million >= 0),
  output_per_million numeric(18,6) NOT NULL CHECK (output_per_million >= 0),
  PRIMARY KEY (provider, model)
);
CREATE TABLE llm_calls (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  run_id uuid, purpose text NOT NULL, provider text NOT NULL, model text NOT NULL, prompt_version text,
  input_tokens integer NOT NULL DEFAULT 0, output_tokens integer NOT NULL DEFAULT 0,
  est_cost_micro bigint,  -- NULL = price not configured (never 0)
  latency_ms integer NOT NULL, outcome text NOT NULL CHECK (outcome IN ('ok','invalid_json','timeout','error')),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_llm_calls_time ON llm_calls (created_at DESC);
CREATE TABLE answer_reports (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  message_id uuid NOT NULL, action_id uuid, reported_by uuid NOT NULL, reason text, status text NOT NULL DEFAULT 'open',
  handoff_id uuid, created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant_id, message_id) REFERENCES messages (tenant_id, id),
  FOREIGN KEY (tenant_id, action_id) REFERENCES agent_actions (tenant_id, id),
  FOREIGN KEY (tenant_id, handoff_id) REFERENCES handoffs (tenant_id, id)
);
CREATE TABLE shadow_verdicts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  action_id uuid NOT NULL, verdict text NOT NULL CHECK (verdict IN ('correct','wrong')), note text,
  by_user uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, action_id),
  FOREIGN KEY (tenant_id, action_id) REFERENCES agent_actions (tenant_id, id)
);

-- ===== 0008 privacy =====
CREATE TABLE consents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  subject_type text NOT NULL CHECK (subject_type IN ('customer','user')), subject_id uuid NOT NULL,
  purpose text NOT NULL CHECK (purpose IN ('whatsapp_optin','ai_processing','email_contact')),
  notice_version text NOT NULL, granted boolean NOT NULL, source text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_consents_subject ON consents (tenant_id, subject_type, subject_id, purpose, created_at DESC);
CREATE TRIGGER consents_no_row_mutation BEFORE UPDATE OR DELETE ON consents FOR EACH ROW EXECUTE FUNCTION append_only();
CREATE TRIGGER consents_no_truncate BEFORE TRUNCATE ON consents FOR EACH STATEMENT EXECUTE FUNCTION append_only();
REVOKE UPDATE, DELETE, TRUNCATE ON consents FROM support_app_runtime;
CREATE TABLE opt_outs (
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  customer_id uuid NOT NULL, channel text NOT NULL CHECK (channel IN ('whatsapp','email')),
  source text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, customer_id, channel),
  FOREIGN KEY (tenant_id, customer_id) REFERENCES customers (tenant_id, id)
);
CREATE TABLE privacy_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  subject_ref text NOT NULL, kind text NOT NULL CHECK (kind IN ('export','delete','correct')),
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','done','rejected')),
  due_at timestamptz NOT NULL DEFAULT now() + interval '30 days', created_at timestamptz NOT NULL DEFAULT now(), done_at timestamptz
);

-- ===== 0009 evals =====
CREATE TABLE eval_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind text NOT NULL CHECK (kind IN ('eval','policy_regression','safety')),
  tenant_id uuid REFERENCES tenants(id),
  git_sha text, prompt_versions jsonb, model text, metrics jsonb NOT NULL, passed boolean NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE policies ADD CONSTRAINT fk_policies_regression FOREIGN KEY (regression_run_id) REFERENCES eval_runs(id);

-- ===== RLS on every tenant table =====
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['integrations','tenant_settings','policies','customers','conversations','inbound_events','messages',
    'usage_counters','jobs','notifications','agent_runs','agent_actions','external_calls','handoffs','llm_calls','answer_reports',
    'shadow_verdicts','consents','opt_outs','privacy_requests'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())', t);
  END LOOP;
  FOREACH t IN ARRAY ARRAY['jobs','llm_calls','integrations','handoffs','agent_runs','notifications','usage_counters','policies','conversations'] LOOP
    EXECUTE format('CREATE POLICY super_admin_read ON %I FOR SELECT USING (app_is_super_admin())', t);
  END LOOP;
END $$;
-- jobs: worker claims across tenants in super-admin context, so it also needs write.
CREATE POLICY super_admin_write ON jobs FOR ALL USING (app_is_super_admin()) WITH CHECK (app_is_super_admin());
ALTER TABLE eval_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE eval_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY eval_runs_rls ON eval_runs USING (tenant_id = app_current_tenant() OR app_is_super_admin())
  WITH CHECK (tenant_id = app_current_tenant() OR app_is_super_admin());
