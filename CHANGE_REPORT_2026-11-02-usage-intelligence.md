# Change report — Enterprise usage metering + billing intelligence (2026-11-02, P23)

Implements the "MailMind AI – Enterprise-Level Usage Metering, Intelligence & Complete
Billing System" spec, entirely inside the existing **Admin → Billing** section (no new
top-level menu), per the spec's own hard requirement. Initially shipped without a test/build
pass per the user's own first instruction; the user then asked for a real test pass after
GitHub Actions CI caught real ruff/mypy issues (see §10) — that full pass is now done and
all green. See PROGRESS.md P23 row for the full reasoning trail; this file is the
section-17-style summary the spec itself asks for.

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

First shipped round skipped execution per the user's own explicit instruction ("TEST MAT
KARNA / KOI TEST MAT KARNA"). The user's own GitHub Actions CI then caught real issues
(`ruff check` B008/F401/I001, `ruff format --check`) and the user asked for a proper test
pass this time. That full pass has now been run, against a real Postgres 16 instance started
in this session, with these **actual results**:

- `ruff check .` (backend) — **All checks passed.** Fixed along the way: 5× `B008` on
  `Query(default="this_month")` (ruff couldn't resolve the custom `DatePreset = Literal[...]`
  type alias as immutable — added `# noqa: B008`, same convention already used on every
  `Depends(...)` line in this file), 2× `F401` unused imports (`LedgerEventType`,
  `UsageMetric` in `usage.py`), 1× `I001` import ordering (auto-fixed).
- `ruff format --check .` (backend) — **249 files formatted correctly** (2 of the new files
  needed reformatting — fixed with `ruff format`).
- `mypy app` (strict mode, 151 source files) — **Success: no issues found.** Fixed along the
  way: `usd_to_paise` arg-type on an empty-`sum()` fallback typed as `Literal[0]` instead of
  `Decimal` (`_gcp_cost_inr_for_tenant` in `usage.py` — now passes `start=Decimal(0)` to
  `sum()`), `client_usage(session, principal.tenant_id)` passing `UUID | None` where `UUID` is
  required (`dashboard.py` — now raises `AppError("E-AUTH-007")` first, same pattern already
  used in `integrations.py`'s `gmail_connect`), one stale `# type: ignore[arg-type]` that mypy
  flagged as unused (removed).
- `pytest -n 2 --dist loadfile` (full backend suite, real Postgres 16) — **657 passed**, 6
  warnings (all pre-existing `httpx` cookie-deprecation warnings, unrelated to this round's
  code), 1366s. Includes `tests/rls/test_ready_and_migrations.py` (the migration-0028 /
  `usage_limits`-table assertions) run against a real up/down/up migration cycle.
- `python -m app.evals.run` (offline-oracle eval harness) — accuracy 1.0, 0 gate failures, 0
  policy-scenario failures across all 224 gate scenarios + 81 tuning rows — unaffected by this
  round's changes (no agent/policy files were touched).
- Frontend: `tsc --noEmit` — clean. `vitest run` — **37 passed (5 files)**. `npm run build`
  (`tsc --noEmit && vite build`) — **built successfully** (`dist/` produced, 426 KB JS /
  41 KB CSS before gzip).

Everything above is a real execution result, not a syntax-only check. The zip in this
delivery is the exact code that produced these results.
