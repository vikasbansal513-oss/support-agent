# Change report - Policy tab: handoff hours, customer handling, return windows (2026-11-12)

All new settings live in `PolicyV2` (policy JSON). No DB migration. Every new field is optional with a
default that keeps the old behaviour, so existing brands are unchanged until they set something.

## New settings (client dashboard -> Policy)
- Agent behaviour: `tone_notes`, `confirm_ttl_minutes` (already in backend, now in the UI).
- Safety dials: `sentiment_min`, `fraud_cluster_window_hours`, `fraud_cluster_claim_limit`
  (already in backend). UI locks them while any write tool is on `auto`; the API does not enforce that lock.
- Handoff: `holidays` (YYYY-MM-DD, office closed) and `day_hours` (per weekday start/end, Mon=0).
  `app/core/officehours.py` (`is_open`, `next_open`) is used by `agent/runner._label`,
  `handoffs/service.sla_due` and `voice/service._in_hours`. With no holidays and no day_hours it calls
  the old `timeutil` functions unchanged. The regression gate and evals still use the plain office_hours rule.
- Return rules: `return_window_by_tag` (tag -> days 1..60, strictest matching tag wins). Used in
  `v1/gate.py` and the decline reply in `v1/graph.py`. `policy_facts` in `v1/reply.py` and the regression
  gate still use `return_window_days`.

## Second batch (same day)
- Store FAQ: `faqs` (max 20, question 3..200, answer 3..400 chars). `v1/reply.py::policy_facts` adds them as
  `store_faq` (only when set), so the existing policy_question answer can repeat them; the reply writer still
  may use only facts and the validator still checks numbers. `app/prompts/plan.v1.md` now lets the planner
  treat general store questions (payment, COD refunds, shipping time, size help) as policy_question. With no FAQ
  set the reply writer says needs_human, so such questions still end in a handoff as before (one extra LLM call).
- Test this policy: `POST /policies/{id}/try` (owner only, `app/policy/simulate.py`). Runs the real `resolve()` gate and
  the office-hours check on a sample order. No LLM call, nothing stored or sent. It tests the SAVED policy.
- Restore: dashboard "Restore this version as a new draft" in Version history (hidden while a draft exists). It uses the
  normal create-draft API, so the same guards apply (for example an old version with more `auto` tools than the
  published one is rejected). Change reason: optional `reason` on create/update draft, stored in the audit log.
- Out-of-office text: `ooo_message` is appended to the handoff acknowledgement when a customer is handed off outside
  office hours (not for the email order-summary layout).
- COD refund: `cod_refund_method` (bank_transfer / store_credit) and `cod_refund_note` go into `policy_facts`. Information
  for replies only. The agent cannot detect COD orders (not in order facts) and no money flow changed.
- Handoff reminder: `handoff_reminder_minutes` (0 = platform default). The existing `sla_check` job uses it as the
  re-alert interval and, only when it is above 0, also emails / WhatsApps the alert contacts from Notifications settings.
  With 0 the job behaves exactly as before.

## Not built (needs product decisions or touches money)
Customer handling (VIP, blocked contacts, repeat limit: dropped by the owner for now), escalation chain, return shipping charge, restocking fee,
language auto-detect, roles, monthly budget cap.

## Deploy order
Backend first (the model forbids unknown fields, so the dashboard cannot save the new fields until the
backend knows them), then the frontend. Do not roll the backend back after a policy with new fields was saved.

## Verified (first batch only)
pytest (full suite) 760 passed, 1 failed: `tests/billing/test_billing_api.py::test_generate_needs_usd_inr_rate_and_a_past_or_current_month`,
which fails the same way on the original zip (it depends on today's date). ruff, ruff format, mypy clean on
app/tests/scripts. `python -m app.evals.run` all zero failures. Frontend: `tsc --noEmit`, vitest (42 passed), `vite build`.

## Known limits

## Second batch: verified
Full backend suite: 762 passed, 1 failed (the same date-dependent billing test as above, also failing on the original zip).
New tests in `tests/v1/test_policy_second_batch.py` (8, all passing): validators, policy_facts, `/try` endpoint, change reason in
audit log, FAQ reaching the reply writer, out-of-office text, handoff reminder email. ruff, ruff format, mypy clean;
`python -m app.evals.run` zero failures; frontend `tsc --noEmit` and vitest (42 passed).
Not covered by tests: the Restore button and the Test panel / FAQ editor UI (only type-checked).
