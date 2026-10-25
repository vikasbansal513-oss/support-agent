-- RLS + constraint tests. Run as the RUNTIME login role (member of support_app_runtime), never as owner/superuser.
--   psql -U app_test -d sa_test -v ON_ERROR_STOP=1 -f rls_tests.sql
\set ON_ERROR_STOP on
\set QUIET on
CREATE FUNCTION pg_temp.ctx(t uuid, su boolean DEFAULT false) RETURNS void LANGUAGE sql AS
$$ SELECT set_config('app.tenant_id', COALESCE(t::text,''), false), set_config('app.is_super_admin', CASE WHEN su THEN 'on' ELSE 'off' END, false) $$;
CREATE FUNCTION pg_temp.expect_fail(sql text, label text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  BEGIN EXECUTE sql; EXCEPTION WHEN OTHERS THEN RETURN; END;
  RAISE EXCEPTION 'FAIL (expected an error): %', label;
END $$;
CREATE FUNCTION pg_temp.expect_rows(sql text, n int, label text) RETURNS void LANGUAGE plpgsql AS $$
DECLARE got int;
BEGIN
  EXECUTE sql; GET DIAGNOSTICS got = ROW_COUNT;
  IF got <> n THEN RAISE EXCEPTION 'FAIL (% rows, expected %): %', got, n, label; END IF;
END $$;
CREATE FUNCTION pg_temp.expect_count(sql text, n bigint, label text) RETURNS void LANGUAGE plpgsql AS $$
DECLARE got bigint;
BEGIN
  EXECUTE sql INTO got;
  IF got <> n THEN RAISE EXCEPTION 'FAIL (count %, expected %): %', got, n, label; END IF;
END $$;

-- ---- 1. role safety (what /ready checks) ----
DO $$ DECLARE r record; BEGIN
  SELECT rolsuper, rolbypassrls INTO r FROM pg_roles WHERE rolname = current_user;
  IF r.rolsuper OR r.rolbypassrls THEN RAISE EXCEPTION 'FAIL: runtime role is superuser/BYPASSRLS'; END IF;
  IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='public' AND tableowner = current_user) THEN
    RAISE EXCEPTION 'FAIL: runtime role owns tables'; END IF;
END $$;

-- ---- 2. every table with tenant_id has RLS ENABLE+FORCE; tables without tenant_id must be on the allowlist ----
DO $$ DECLARE bad text; BEGIN
  SELECT string_agg(c.relname, ',') INTO bad FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   JOIN pg_attribute a ON a.attrelid=c.oid AND a.attname='tenant_id' AND NOT a.attisdropped
   WHERE n.nspname='public' AND c.relkind='r' AND NOT (c.relrowsecurity AND c.relforcerowsecurity);
  IF bad IS NOT NULL THEN RAISE EXCEPTION 'FAIL: tenant tables without RLS ENABLE+FORCE: %', bad; END IF;
  SELECT string_agg(c.relname, ',') INTO bad FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND c.relkind='r'
     AND NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attname='tenant_id' AND NOT a.attisdropped)
     AND c.relname NOT IN ('users','platform_admins','email_tokens','refresh_tokens','rate_limits','user_mfa','tenants',
                           'llm_model_prices','heartbeats');
  IF bad IS NOT NULL THEN RAISE EXCEPTION 'FAIL: tables without tenant_id not on the global allowlist: %', bad; END IF;
  SELECT string_agg(c.relname, ',') INTO bad FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND c.relkind='r' AND c.relname IN ('tenants','memberships') AND NOT (c.relrowsecurity AND c.relforcerowsecurity);
  IF bad IS NOT NULL THEN RAISE EXCEPTION 'FAIL: tenants/memberships RLS missing: %', bad; END IF;
END $$;

-- ---- 3. fixtures (super admin context creates two tenants) ----
SELECT pg_temp.ctx(NULL, true);
INSERT INTO tenants (id, name, slug) VALUES ('aaaaaaaa-0000-0000-0000-00000000000a','Brand A','brand-a'),('bbbbbbbb-0000-0000-0000-00000000000b','Brand B','brand-b');
SELECT pg_temp.ctx('aaaaaaaa-0000-0000-0000-00000000000a');
INSERT INTO customers (id, phone_e164) VALUES ('a1111111-0000-0000-0000-000000000001','+919811100001');
INSERT INTO conversations (id, customer_id, channel) VALUES ('a2222222-0000-0000-0000-000000000001','a1111111-0000-0000-0000-000000000001','whatsapp');
INSERT INTO messages (conversation_id, direction, sender, channel, content) VALUES ('a2222222-0000-0000-0000-000000000001','in','customer','whatsapp','order kahan hai');
SELECT pg_temp.ctx('bbbbbbbb-0000-0000-0000-00000000000b');
INSERT INTO customers (id, phone_e164) VALUES ('b1111111-0000-0000-0000-000000000001','+919822200002');

-- ---- 4. isolation ----
SELECT pg_temp.ctx(NULL, false);
SELECT pg_temp.expect_count('SELECT count(*) FROM customers', 0, 'no context sees 0 customers');
SELECT pg_temp.expect_count('SELECT count(*) FROM messages', 0, 'no context sees 0 messages');
SELECT pg_temp.expect_fail($$INSERT INTO customers (phone_e164) VALUES ('+919833300003')$$, 'insert without context');
SELECT pg_temp.ctx('bbbbbbbb-0000-0000-0000-00000000000b');
SELECT pg_temp.expect_count('SELECT count(*) FROM customers', 1, 'B sees only own customer');
SELECT pg_temp.expect_count('SELECT count(*) FROM messages', 0, 'B sees none of As messages');
SELECT pg_temp.expect_rows($$UPDATE customers SET email_lower='x@y.in' WHERE id='a1111111-0000-0000-0000-000000000001'$$, 0, 'B cannot update A customer');
SELECT pg_temp.expect_rows($$DELETE FROM customers WHERE id='a1111111-0000-0000-0000-000000000001'$$, 0, 'B cannot delete A customer');
SELECT pg_temp.expect_fail($$INSERT INTO customers (tenant_id, phone_e164) VALUES ('aaaaaaaa-0000-0000-0000-00000000000a','+919844400004')$$, 'B inserting with tenant_id=A');
SELECT pg_temp.expect_fail($$INSERT INTO conversations (customer_id, channel) VALUES ('a1111111-0000-0000-0000-000000000001','whatsapp')$$, 'B conversation pointing at A customer (composite FK)');
SELECT pg_temp.expect_count('SELECT count(*) FROM tenants', 1, 'B sees only its own tenant row');
SELECT pg_temp.ctx('aaaaaaaa-0000-0000-0000-00000000000a');
SELECT pg_temp.expect_count('SELECT count(*) FROM messages', 1, 'A sees own message');
SELECT pg_temp.ctx(NULL, true);
SELECT pg_temp.expect_count('SELECT count(*) FROM tenants', 2, 'super admin sees all tenants');
SELECT pg_temp.expect_count('SELECT count(*) FROM customers', 0, 'super admin does NOT automatically see customers');

-- ---- 5. transaction-local context does not leak (pooler safety) ----
SELECT set_config('app.tenant_id', '', false);
BEGIN;
SELECT set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-00000000000a', true);
SELECT pg_temp.expect_count('SELECT count(*) FROM customers', 1, 'inside txn context active');
COMMIT;
SELECT pg_temp.expect_count('SELECT count(*) FROM customers', 0, 'after COMMIT context gone (set_config is_local)');

-- ---- 6. append-only tables ----
SELECT pg_temp.ctx('aaaaaaaa-0000-0000-0000-00000000000a');
INSERT INTO audit_log (action, entity_type) VALUES ('test.audit','x');
SELECT pg_temp.expect_fail($$UPDATE audit_log SET action='tampered'$$, 'audit UPDATE');
SELECT pg_temp.expect_fail($$DELETE FROM audit_log$$, 'audit DELETE');
SELECT pg_temp.expect_fail($$TRUNCATE audit_log$$, 'audit TRUNCATE');
SELECT pg_temp.ctx('bbbbbbbb-0000-0000-0000-00000000000b');
SELECT pg_temp.expect_count('SELECT count(*) FROM audit_log', 0, 'B cannot read As audit');
SELECT pg_temp.expect_fail($$INSERT INTO audit_log (tenant_id, action) VALUES ('aaaaaaaa-0000-0000-0000-00000000000a','forged')$$, 'B forging audit for A');
SELECT pg_temp.ctx('aaaaaaaa-0000-0000-0000-00000000000a');
INSERT INTO consents (subject_type, subject_id, purpose, notice_version, granted, source) VALUES ('customer','a1111111-0000-0000-0000-000000000001','whatsapp_optin','v1',true,'webhook');
SELECT pg_temp.expect_fail($$UPDATE consents SET granted=false$$, 'consents UPDATE');
SELECT pg_temp.expect_fail($$DELETE FROM consents$$, 'consents DELETE');
INSERT INTO consents (subject_type, subject_id, purpose, notice_version, granted, source) VALUES ('customer','a1111111-0000-0000-0000-000000000001','whatsapp_optin','v1',false,'stop_keyword');
SELECT pg_temp.expect_count('SELECT count(*) FROM consents', 2, 'withdraw = new row');

-- ---- 7. idempotency and uniqueness (DB-level, not app-level) ----
INSERT INTO inbound_events (channel, external_id, raw_payload) VALUES ('whatsapp','wamid.1','{}');
SELECT pg_temp.expect_fail($$INSERT INTO inbound_events (channel, external_id, raw_payload) VALUES ('whatsapp','wamid.1','{}')$$, 'duplicate inbound event');
INSERT INTO inbound_events (channel, external_id, raw_payload) VALUES ('whatsapp','wamid.1x','{}') ON CONFLICT (tenant_id, channel, external_id) DO NOTHING;
SELECT pg_temp.expect_rows($$INSERT INTO inbound_events (channel, external_id, raw_payload) VALUES ('whatsapp','wamid.1','{}') ON CONFLICT (tenant_id, channel, external_id) DO NOTHING$$, 0, 'ON CONFLICT DO NOTHING returns 0');
INSERT INTO agent_runs (id, inbound_event_id, conversation_id, graph_version, mode) SELECT 'a3333333-0000-0000-0000-000000000001', id, 'a2222222-0000-0000-0000-000000000001', 'g1', 'shadow' FROM inbound_events WHERE external_id='wamid.1';
INSERT INTO agent_actions (run_id, conversation_id, action_type, idempotency_key, status, recipient, body_text) VALUES ('a3333333-0000-0000-0000-000000000001','a2222222-0000-0000-0000-000000000001','reply_customer','run1:reply_customer:1','claimed','+919811100001','hi');
SELECT pg_temp.expect_fail($$INSERT INTO agent_actions (conversation_id, action_type, idempotency_key, status, recipient) VALUES ('a2222222-0000-0000-0000-000000000001','reply_customer','run1:reply_customer:1','claimed','+919811100001')$$, 'duplicate idempotency key (two workers)');
SELECT pg_temp.expect_fail($$INSERT INTO agent_actions (conversation_id, action_type, idempotency_key, status) VALUES ('a2222222-0000-0000-0000-000000000001','reply_customer','k2','claimed')$$, 'reply without recipient');
SELECT pg_temp.expect_fail($$INSERT INTO agent_runs (inbound_event_id, conversation_id, graph_version, mode) SELECT id,'a2222222-0000-0000-0000-000000000001','g1','shadow' FROM inbound_events WHERE external_id='wamid.1'$$, 'second run for same inbound event');
SELECT pg_temp.expect_fail($$INSERT INTO conversations (customer_id, channel) VALUES ('a1111111-0000-0000-0000-000000000001','whatsapp')$$, 'second open conversation for same customer+channel');
INSERT INTO handoffs (conversation_id, reason_code, summary) VALUES ('a2222222-0000-0000-0000-000000000001','HUMAN_REQUESTED','{}');
SELECT pg_temp.expect_fail($$INSERT INTO handoffs (conversation_id, reason_code, summary) VALUES ('a2222222-0000-0000-0000-000000000001','TOOL_FAILED','{}')$$, 'second open handoff');
SELECT pg_temp.expect_fail($$INSERT INTO customers (phone_e164) VALUES ('+919811100001')$$, 'duplicate phone in tenant');
SELECT pg_temp.expect_fail($$INSERT INTO customers (phone_e164) VALUES ('9811100009')$$, 'phone not E.164');
INSERT INTO jobs (kind, idempotency_key) VALUES ('process_inbound','inb:1');
SELECT pg_temp.expect_rows($$INSERT INTO jobs (kind, idempotency_key) VALUES ('process_inbound','inb:1') ON CONFLICT (tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING$$, 0, 'job idempotency');

-- ---- 8. policies: one published, needs regression run ----
SELECT pg_temp.ctx(NULL, true);
INSERT INTO eval_runs (id, kind, metrics, passed) VALUES ('e0000000-0000-0000-0000-000000000001','policy_regression','{}',true);
SELECT pg_temp.ctx('aaaaaaaa-0000-0000-0000-00000000000a');
INSERT INTO policies (version, status, content, content_hash) VALUES (1,'draft','{}','h1'),(2,'draft','{}','h2');
SELECT pg_temp.expect_fail($$UPDATE policies SET status='published' WHERE version=1$$, 'publish without regression run/approver');
UPDATE policies SET status='published', published_by='a1111111-0000-0000-0000-000000000001', published_at=now(), regression_run_id='e0000000-0000-0000-0000-000000000001' WHERE version=1;
SELECT pg_temp.expect_fail($$UPDATE policies SET status='published', published_by='a1111111-0000-0000-0000-000000000001', published_at=now(), regression_run_id='e0000000-0000-0000-0000-000000000001' WHERE version=2$$, 'two published policies');

-- ---- 9. webhook number lookup table ----
INSERT INTO wa_numbers (source_number, webhook_token_hash, tenant_id) VALUES ('919900000001',repeat('a',64),'aaaaaaaa-0000-0000-0000-00000000000a');
SELECT pg_temp.expect_fail($$INSERT INTO wa_numbers (source_number, webhook_token_hash, tenant_id) VALUES ('919900000001',repeat('b',64),'bbbbbbbb-0000-0000-0000-00000000000b')$$, 'B claiming As WhatsApp number');
SELECT pg_temp.ctx(NULL, false);
SELECT pg_temp.expect_count($$SELECT count(*) FROM wa_numbers WHERE source_number='919900000001'$$, 1, 'webhook lookup works without tenant context');
SELECT pg_temp.expect_count($$SELECT count(*) FROM wa_numbers WHERE webhook_token_hash=repeat('a',64) AND tenant_id='aaaaaaaa-0000-0000-0000-00000000000a'$$, 1, 'webhook lookup by token hash returns owning tenant');
SELECT pg_temp.expect_count($$SELECT count(*) FROM wa_numbers WHERE webhook_token_hash=repeat('f',64)$$, 0, 'unknown token hash finds nothing');
SELECT pg_temp.expect_fail($$INSERT INTO wa_numbers (source_number, webhook_token_hash, tenant_id) VALUES ('919900000009',repeat('c',64),'aaaaaaaa-0000-0000-0000-00000000000a')$$, 'no-context insert into wa_numbers');
SELECT pg_temp.ctx('aaaaaaaa-0000-0000-0000-00000000000a');
SELECT pg_temp.expect_fail($$INSERT INTO wa_numbers (source_number, webhook_token_hash) VALUES ('919900000003','not-a-hash')$$, 'token hash must be sha256 hex');
SELECT pg_temp.expect_fail($$INSERT INTO wa_numbers (source_number, webhook_token_hash, tenant_id) VALUES ('919900000002',repeat('d',64),'aaaaaaaa-0000-0000-0000-00000000000a')$$, 'one number per tenant');

-- ---- 10. jobs: worker claim across tenants (super admin) with SKIP LOCKED ----
SELECT pg_temp.ctx('bbbbbbbb-0000-0000-0000-00000000000b');
INSERT INTO jobs (kind, idempotency_key) VALUES ('process_inbound','inb:b1');
SELECT pg_temp.expect_count('SELECT count(*) FROM jobs', 1, 'B sees only its own job');
SELECT pg_temp.ctx(NULL, true);
SELECT pg_temp.expect_count('SELECT count(*) FROM jobs', 2, 'worker (super admin) sees jobs of both tenants');
SELECT pg_temp.expect_rows($$UPDATE jobs SET status='processing', lease_owner='w1', lease_expires_at=now()+interval '2 min', attempts=attempts+1 WHERE id=(SELECT id FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)$$, 1, 'claim one job');
SELECT pg_temp.expect_fail($$UPDATE jobs SET status='processing' WHERE status='queued'$$, 'processing needs a lease (check constraint)');

\echo ALL RLS/CONSTRAINT TESTS PASSED
