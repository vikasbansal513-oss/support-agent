# Change report — Enterprise usage metering + billing intelligence (2026-11-02, P23)

Implements the "MailMind AI – Enterprise-Level Usage Metering, Intelligence & Complete
Billing System" spec, entirely inside the existing **Admin → Billing** section (no new
top-level menu), per the spec's own hard requirement. No tests/build were run this session —
the user explicitly asked for the finished zip directly, without a test/build pass. See
PROGRESS.md P23 row for the full reasoning trail; this file is the section-17-style summary
the spec itself asks for.

## 1. Files modified

- `backend/app/api/admin_billing.py` — 14 new routes appended under the same `/admin/billing`
  prefix (nothing existing removed or changed).
- `backend/app/api/dashboard.py` — 1 new route (`GET /dashboard/billing-usage`) appended.
- `backend/app/core/alerts.py` — 1 new import + 1 new check block appended to the end of
  `scan_and_upsert()` (existing checks untouched).
- `backend/tests/rls/test_ready_and_migrations.py` — the hardcoded table-name list now
  includes `usage_limits`, and the alembic-head assertion moved `"0027"` → `"0028"` (these are
  consistency edits to match the new migration, not a test run — see PROGRESS.md).
- `frontend/src/pages/Billing.tsx` — added a 4th tab button ("Usage Intelligence") and a
  ternary that renders the new `<BillingIntel/>` instead of the existing month/period view when
  that tab is active. The existing Overview/Invoices/Usage tabs' code paths are untouched.
- `frontend/src/pages/Dashboard.tsx` — appended one new `<Async>` card ("My usage & cost (this
  month)") at the end of the page; nothing existing changed.
- `frontend/src/api/types.ts` — new TS interfaces appended (nothing existing changed).
- `frontend/src/i18n.ts` — new keys appended (nothing existing changed/removed).

## 2. Files created

- `backend/migrations/versions/0028_usage_metering.py`
- `backend/app/billing/usage_schemas.py` — Pydantic schemas for the whole feature.
- `backend/app/billing/usage.py` — all query/service logic.
- `frontend/src/pages/BillingIntel.tsx` — the 5 sub-tab UI (Overview/Clients/Ledger/
  Reports/Alerts & Limits), embedded (no own page title) so it nests inside the existing
  Billing page.

## 3. Database migrations

Migration `0028`:
- `billing_settings` gets 3 new columns: `billing_export_last_sync_at`,
  `billing_export_last_sync_rows`, `billing_export_sync_error` (status fields for the GCP
  Billing Export panel — see §6).
- New table `usage_limits` (admin-only RLS, same pattern as `tenant_billing_profiles`):
  per-client monthly limit on one of `messages_month` / `total_cost_inr_month` /
  `llm_cost_inr_month`.

No other new tables. Message usage, agent execution, LLM usage, Plivo CDR, STT/TTS usage and
GCP app-level usage all already existed as tables from earlier phases (`messages`,
`agent_runs`+`agent_actions`, `llm_calls`, `plivo_call_records`, `speech_usage_records`,
`gcp_usage_events`) — this round only added the *queries* that read them over an arbitrary
date range instead of a fixed calendar month, plus the two things above.

## 4. APIs added

All under `/admin/billing` (admin-only), plus one under the existing client-facing dashboard
router:

- `GET /admin/billing/overview` — platform-wide revenue/cost/profit/margin for a date range.
- `GET /admin/billing/clients` — per-client summary list for a date range.
- `GET /admin/billing/clients/{tenant_id}` — full client-wise breakdown (messages, agent
  performance, LLM/GCP usage, other service costs, financial summary).
- `GET /admin/billing/usage-ledger` + `GET /admin/billing/usage-ledger/export.csv` — unified
  cross-service ledger, filterable, CSV export.
- `GET /admin/billing/reports/{report_type}` + `.../export.csv` — 8 report types.
- `GET/PUT /admin/billing/usage-limits`, `GET /admin/billing/usage-limits/status` — per-client
  limits and current-month %-used.
- `GET /admin/billing/gcp/export-status` — honest BigQuery Billing Export connection/sync
  status (never fakes "Connected").
- `GET /dashboard/billing-usage` — client's own current-month usage/cost (tenant-locked in app
  code, not by the caller).

## 5. Billing calculations implemented

- LLM cost per client for any date range (reuses the existing rate/call-log pricing logic).
- Cloud Run compute cost per client for any date range (reuses the existing vCPU/GiB formula).
- Plivo (calls + number rental), STT/TTS, and GCP app-level-metering cost per client for any
  date range (new range queries over the existing tables).
- Revenue = finalized, non-cancelled invoices with `invoice_date` in range.
- Profit = revenue − (LLM + Cloud Run + Plivo + STT/TTS + other-GCP cost); margin = profit /
  revenue × 100.
- Agent performance: total/completed/failed/pending tasks, success rate, avg/total execution
  time, retry count (`agent_actions.attempts`), cost-per-successful-task.
- An unconfigured USD→INR rate or LLM rate is never silently shown as ₹0 — it is flagged
  (`unpriced_llm_models`, or a note on the Overview) exactly like the existing invoice flow
  already does.

## 6. Google Cloud Billing Export integration status

**Not** wired to the real BigQuery API this round — confirmed with the user before building:
they chose "structure + the existing manual import" over a live BigQuery pull (which would
need a GCP service-account/credentials handed over in this session). What exists:
- Status panel (`/admin/billing/gcp/export-status`) reports `billing_settings`'s actual
  `billing_export_status`/`billing_export_ref`, last-sync time/row-count/error (new columns),
  and how many import batches have been received (`gcp_billing_import_rows`, grouped by
  `import_batch_id` — that grouping *is* the import history, no new table needed for it).
- Rows still arrive via the existing `POST /admin/billing/gcp/import` (manual paste/CSV or a
  script you run against it) — unchanged from before this session.
- It never reports "Connected" unless `billing_settings.billing_export_status` genuinely says
  so.

## 7. Features completed

Everything in the spec's sections A–J (Overview, Client-wise Billing, Usage Ledger, Message
tracking, Agent execution tracking, LLM usage/billing, GCP usage/billing surfaced honestly,
Billing Settings reused as-is, central calc engine, Reports, Usage limits/alerts reusing the
existing alerts table, client dashboard own-usage view, tenant isolation on every new query) —
implemented and wired into the existing Admin → Billing section as one more tab, per the
spec's explicit instruction not to add a new top-level menu.

## 8. Features still pending / requiring further infra

- Real BigQuery Billing Export API pull + scheduled sync (needs GCP service-account
  credentials + project/dataset access — by the user's own choice, deferred this round).
- A real `.xlsx` export (this project has no `openpyxl`/`xlsxwriter` dependency; "Excel
  export" here is a CSV, which Excel/Sheets opens natively — same convention the existing
  audit-log export already uses).
- "Active Brands" is reported as a constant `1` per client — this platform models one brand
  per tenant with no separate multi-brand sub-entity in the schema; a real multi-brand count
  would need a new `brands` table, which is out of scope for a reuse-first pass.
- "Subscription/Maintenance Plan" reuses `tenant_billing_profiles.billing_status`
  (`enabled`/`paused`) as there is no separate subscription-plan table in this project.
- Whole-Cloud-Run-fleet CPU/memory and raw inbound HTTP request-rate remain out of scope,
  same as documented in the Control Tower work (P22) — this round did not change that.

## 9. External credentials/permissions that would be required next

- A GCP service account with BigQuery read access to the Billing Export dataset, if/when the
  real automated sync is built.
- Nothing else new — everything else in this round reuses credentials/settings the project
  already has a place for (Plivo, LLM provider rates, GST/company details).

## 10. Tests executed and their actual results

**None.** Per explicit user instruction this session ("TEST MAT KARNA / KOI TEST MAT KARNA"),
no `pytest`, `vitest`, `tsc`, `ruff`, `mypy`, or build was run. Every earlier phase (P16–P22)
ran the full suite before shipping; this one did not, on direct instruction. What was done
instead, as a substitute for execution:
- Every new SQL query's column/table names were checked against the actual migration files
  and `service.py`'s existing queries before being written (not assumed from memory).
- The one hardcoded table-list test and the alembic-head test were updated to match the new
  migration (source-of-truth edits, not a test run).
- Icon names used in the two touched frontend files were checked against the actual sprite in
  `Icons.tsx` rather than guessed.
- The new `BillingIntel.tsx` tab is wired so it only renders instead of — never alongside or
  inside — the existing month/period view, to keep the blast radius on `Billing.tsx` to two
  small, additive edits.

**Before this goes anywhere near production, run:** `alembic upgrade head` against a real
Postgres 16 (watch for the same FORCE-RLS seed-insert class of bug hit in 0025/0027, though
0028 has no seed INSERT so it may not apply), the full backend `pytest` suite, and frontend
`tsc --noEmit` / `vitest run` / `npm run build`.
