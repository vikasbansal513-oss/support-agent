# Load testing (k6)

This folder closes the DevOps audit's Section 1 gap: there was no load/stress test
anywhere in the repo, so the `LLM_P95_TARGET_MS` / `api_latency_p95_ms` numbers used
elsewhere in the code (core/alerts.py, monitoring_thresholds) were never actually
checked against real concurrent traffic - only inferred from production's organic
volume after the fact.

**These scripts are write-only on purpose.** Nothing in CI, `deploy.yml`, or any
schedule runs them. You decide when and where, by running `k6 run ...` yourself or by
dispatching `.github/workflows/loadtest.yml` manually.

## Before you run anything against a real environment

- **Never point these at production with real customer data**, except
  `dashboard_read.js` which is read-only and safe against a real environment (it still
  logs in a real user `VUS` times, so prefer a dedicated test account).
- `webhook_whatsapp.js` inserts real `inbound_events`/`jobs` rows and, if
  `INSTANT_DRAIN_ENABLED=true` and a real LLM key is configured, will call your real LLM
  provider for every single simulated message - this costs real money and counts
  against real quota at whatever `VUS` x `DURATION` you choose. Start small (`VUS=5`)
  against staging before scaling up.
- `approval_write.js` calls a **real** Shopify/WooCommerce write API on success
  (cancel/refund) for each seeded action id. Only ever point this at a disposable test
  tenant with fake orders you are fine being cancelled/refunded for real. This is the
  one script that can touch real money/inventory if misused.

The right environment for all three is a staging Cloud Run service with its own test
tenant and test Shopify/WooCommerce dev store - not production. If you don't have a
staging environment yet, standing one up (a second Cloud Run deploy, same image,
pointed at a disposable Postgres database and a Shopify dev store) is worth doing before
your first real run, precisely so these scripts have somewhere safe to run.

## Scripts

| Script | What it tests | Profile | Can write real data? |
|---|---|---|---|
| `webhook_whatsapp.js` | `POST /webhooks/whatsapp/{token}` - the hot customer-facing path, including the inline-drain agent pipeline | ramping VUs, sustained load | Yes - real `inbound_events`/`jobs` rows, real LLM calls if instant drain is on |
| `dashboard_read.js` | `POST /auth/login` + `GET /dashboard/stats` + `GET /approvals` - the authenticated dashboard session pattern | ramping VUs, sustained load | No - read-only after login |
| `approval_write.js` | `POST /approvals/{id}/approve` - the write-confirm path that calls a real Shopify/WooCommerce write API | one pass over a seeded list of action ids, 1 VU | **Yes - real order cancel/refund** |

## Running locally

Install k6 (`brew install k6`, or see https://k6.io/docs/get-started/installation/),
then:

```bash
# Hot path
BASE_URL=https://staging-xyz.a.run.app WEBHOOK_TOKEN=<tenant webhook token> \
  k6 run loadtest/webhook_whatsapp.js

# Dashboard reads
BASE_URL=https://staging-xyz.a.run.app LOGIN_EMAIL=you@example.com LOGIN_PASSWORD='...' \
  k6 run loadtest/dashboard_read.js

# Write-confirm path (read the warning above first)
BASE_URL=https://staging-xyz.a.run.app LOGIN_EMAIL=owner@example.com LOGIN_PASSWORD='...' \
  ACTION_IDS=11111111-1111-1111-1111-111111111111,22222222-2222-2222-2222-222222222222 \
  k6 run loadtest/approval_write.js
```

Each script fails fast with a clear error if a required env var is missing - see the
comment block at the top of each file for the full list and defaults (`VUS`,
`DURATION`, `P95_MS`, etc).

## Running from GitHub Actions

`.github/workflows/loadtest.yml` is `workflow_dispatch`-only (never auto-triggered,
same guard pattern as `deploy.yml`). Dispatch it from the Actions tab, choose the
script, the target `base_url`, and type `RUN` in the confirm field. Before the first
dispatch of each script, set these as **repo secrets** (Settings > Secrets and
variables > Actions) - they're credentials, so they don't belong in plain repo
variables: `LOADTEST_WEBHOOK_TOKEN`, `LOADTEST_LOGIN_EMAIL`, `LOADTEST_LOGIN_PASSWORD`,
`LOADTEST_ACTION_IDS` (comma-separated).

## Reading the results

k6 prints a summary at the end of the run (p50/p95/p99, error rate, iteration count).
The thresholds baked into each script (`options.thresholds`) make k6 itself exit
non-zero on failure, so a GitHub Actions run going red is itself the signal, no manual
reading required. For a deeper look, pipe k6's JSON output
(`k6 run --out json=result.json ...`) into whatever you already use to graph it, or
compare the printed p95 against the live number for the same window in
`monitoring_thresholds`/Control Tower (`core/alerts.py`'s `integration_latency_high` and
`agent_run_failure_spike` checks) to see whether synthetic load and real production
numbers actually agree.
