# Change report — 2026-11-02

Two pieces of work, delivered together per your confirmation ("Dono ek saath"):

1. **English-only customer replies** — WhatsApp/email replies always go out in English now, regardless of the language the customer wrote in.
2. **Control Tower advanced monitoring** — the Admin Control Tower is now a full AI-agent-performance + system-monitoring center, per the attached spec, with the hard rule "never show fabricated production metrics" followed throughout.

See `DECISIONS.md` (2026-11-02 entries) and `PROGRESS.md` (P22 row) for the detailed reasoning trail; this file is the section-12-style summary.

## 1. English-only customer replies

**Root fix**: `backend/app/agent/validate.py::pick_language()` now always returns `"en"` — this is the single choke point every V0 and V1 reply/outcome-notify path already funneled through, so one change covers both engines without touching templates.

**Also edited**: `backend/app/prompts/reply.v1.md` (language instruction + few-shot example, now English).

**Deliberately untouched**: Voice/calling (`app/voice/service.py`, `app/voice/speech.py`) — it uses the caller's detected speech language directly, not `pick_language()`, and your request was about WhatsApp/email text messages. Changing voice TTS language was out of scope and risked unrelated UX breakage.

Dashboard UI was already fully English (checked `frontend/src/i18n.ts` — no change needed there).

## 2. Control Tower advanced monitoring

### New database objects (migration `0027`)
- `page_perf_events` — one row per page navigation (Web Vitals beacon), tenant-scoped RLS.
- `monitoring_thresholds` — global, admin-only RLS, 8 seeded alert thresholds, admin-editable.
- `shadow_verdicts.verdict` constraint extended with `'partial'` (additive).

### Pre-existing bugs fixed (matching the spec's own example)
Three places were labeled "failed jobs" but actually queried `status = 'needs_review'`:
`AdminHealthOut.jobs_failed_today`, `OverviewOut.jobs_failed`, `ControlTowerOut.failed_jobs_today`/`failed_jobs_avg_7d`.
Fix: kept the field names (so nothing calling them breaks), corrected the underlying query to really mean "failed", and added new, correctly-named fields (`jobs_needs_review_today`, `jobs_needs_review`, `needs_review_jobs_today`/`needs_review_jobs_avg_7d`) for the separate "needs a human look" bucket. Same mislabeling existed in `core/alerts.py`'s alert checks — fixed there too (renamed the old check to `job_needs_review_spike`, added a genuinely new `job_failure_spike`).

### New backend modules
- `app/monitoring/queries.py` — all read queries: agent task list/detail, accuracy, page performance, traffic, historical series. Every number is read from an existing table or live Postgres/process state — nothing invented. Doc comment at top of the file lists exactly which tables back which number.
- `app/monitoring/perf_ingest.py` — single-row insert for a Web Vitals beacon.
- `app/api/admin_monitoring.py` — `GET /admin/monitoring/agent-tasks[/{id}]`, `/accuracy`, `/page-performance`, `/traffic`, `/historical`, `GET`+`PUT /thresholds`.
- `app/api/perf.py` — `POST /perf/beacon` (authenticated, non-blocking ingest from the dashboard).
- `app/core/alerts.py` — 1 renamed/fixed check + 4 new checks (queue backlog, per-tenant accuracy, per-route slow page, DB connections), all configurable via `monitoring_thresholds`.

### What was deliberately NOT built (to avoid fabricating data)
- No new periodic worker job or time-series table for infra metrics — traffic/infra numbers are computed **live** on each admin request (same convention the existing `/admin/control-tower` endpoint already used). Trade-off: no historical CPU/mem/DB-connection trend lines yet — stated plainly in the API's own `TrafficOut.note` field, not hidden.
- No new HTTP-request-logging middleware — "API performance" reuses the existing `external_calls` table (outbound Shopify/courier/WhatsApp/Gmail call latency). Raw inbound request-rate isn't measured; documented in the same `note` field.
- `process_rss_mb` is this one API process's memory (`resource.getrusage`), not the whole Cloud Run fleet's — fleet-wide metrics need Cloud Monitoring API integration, which isn't wired.

### Frontend
- `frontend/src/lib/webVitals.ts` — new, hand-rolled Web Vitals collector (Navigation Timing + `PerformanceObserver` for LCP/CLS/INP; no new npm dependency), fire-and-forget POST to `/perf/beacon`.
- `frontend/src/components/Layout.tsx` — wired to send a beacon on every route change, for the client/owner workspace only (not the admin panel's own pages — "client page performance" means the tenant's experience).
- `frontend/src/pages/AdminControlTower.tsx` — rewritten with 8 sections: System Health (existing, preserved, + needs-review stat cards added), AI Agent Performance/Accuracy (window selector + per-client breakdown), Agent Task History (filters, pagination, inline drill-down dialog with trace/decision/actions/LLM calls/verdict), Client Page Performance, Traffic & System Load + Integration/API Performance, Historical Analytics (inline SVG sparklines, no new chart library, 24h/7d/30d), Alerts (links to the existing `AdminAlerts.tsx` page rather than duplicating it), Monitoring Thresholds (admin-editable).
- `frontend/src/api/types.ts`, `frontend/src/i18n.ts` — extended with matching TS types and English-only labels for everything new.

### Existing functionality reused, not duplicated
`agent_runs.decision` (task category), `shadow_verdicts`+`answer_reports` (accuracy), `external_calls` (API latency), `audit_log` (active-users proxy), `pg_stat_activity`/`pg_settings` (DB connections), the existing lazy `scan_and_upsert()` alert pattern, the existing `AdminAlerts.tsx` page (linked, not rebuilt).

## Testing performed
- Migration `0027` run against a real Postgres 16 instance. Hit and fixed the same RLS-seed-insert bug class the project had already hit once before for `billing_cost_categories` (`FORCE ROW LEVEL SECURITY` blocks the migration's own seed `INSERT` unless `SET LOCAL app.is_super_admin = 'on'` is set first).
- `tests/rls/test_ready_and_migrations.py` — updated and passing (7/7) against real Postgres.
- `ruff check .`, `ruff format --check .`, `mypy` — all clean on the new/changed backend files (one new `S608` per-file-ignore added for `app/monitoring/queries.py`, matching the existing pattern already used for similar parameterized-but-flagged queries elsewhere in the codebase).
- Full backend suite (`pytest -q -n4 --dist loadfile`, 650+ tests) run against real Postgres — see the note at the end of this file for the final pass/fail count.
- Frontend: `tsc --noEmit` clean, `npm run build` clean, `vitest run` — 37/37 passed.

## Deployment / migration instructions
1. `cd backend && alembic upgrade head` — applies migration `0027` (adds `page_perf_events`, `monitoring_thresholds`, extends `shadow_verdicts.verdict`).
2. No new environment variables or dependencies (backend or frontend) were added.
3. Redeploy the frontend as usual — `npm run build` output is unchanged in shape, just a new page section.

## Features fully implemented vs. requiring further infra/config
**Fully implemented, real data**: agent task history + drill-down, accuracy (correct/wrong/partial/unverified), per-route page-load Web Vitals, DB connections, queue depth, running/failed/needs-review job counts, integration call latency/error-rate, historical 24h/7d/30d series, configurable alert thresholds.

**Documented limitation, not faked**: whole-Cloud-Run-fleet CPU/memory (needs Cloud Monitoring API wiring); historical CPU/mem/DB-connection trend lines (would need a new periodic sampler + time-series table — deliberately not built this session to avoid extra always-on DB writes without being asked); raw inbound HTTP request-rate (would need new request-logging middleware).
