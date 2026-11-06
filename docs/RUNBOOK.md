# Runbook

## Moving parts
- API: Cloud Run service `support-agent` (min instances 0).
- Worker: Cloud Run Job `support-agent-worker`, triggered every minute by Cloud Scheduler `support-agent-worker-tick`. Each run lasts at most 55 s.
- Migrations: Cloud Run Job `support-agent-migrate` (`alembic upgrade head`), run by deploy.yml before the service is updated.
- Database: Postgres (Neon). Runtime role `support_app_runtime` (no superuser, not table owner). Migration role is separate.
- Secrets: Secret Manager only. Names: database-url, migration-database-url, encryption-key, jwt-secret, admin-jwt-secret, llm-api-key, brevo-api-key, google-client-id, google-client-secret, sentry-dsn.

## One-time setup (per environment)
1. Create the Artifact Registry repo, the runtime service account, the deploy service account and the scheduler service account.
2. Configure Workload Identity Federation for GitHub; set the repo variables GCP_PROJECT_ID, GCP_REGION, GCP_ARTIFACT_REPO, GCP_WIF_PROVIDER, GCP_DEPLOY_SA, GCP_RUNTIME_SA, GCP_SCHEDULER_SA, FRONTEND_URL, PUBLIC_BASE_URL, MAIL_FROM, GRIEVANCE_EMAIL, LLM_MODEL.
3. Grant the runtime SA `secretmanager.secretAccessor`; the scheduler SA `run.invoker` on the worker job; the deploy SA `run.admin`, `artifactregistry.writer`, `iam.serviceAccountUser`, `cloudscheduler.admin`.
4. Create all secrets. `ENCRYPTION_KEY` is a 32-byte urlsafe base64 key; losing it makes stored integration secrets unreadable, so back it up separately.
5. Apply `infra/monitoring/alert-policies.yaml` (one policy per YAML document) and attach a notification channel.
6. Set `NOTICE_PUBLISHED=true` only after the privacy notice and terms have had legal review.
7. Run deploy.yml with `confirm=DEPLOY`.

## Deploy and rollback
- Deploy: Actions, run `deploy`, type DEPLOY, choose environment. The pipeline tests, builds, migrates, deploys the API and worker, upserts the scheduler, then runs `scripts/smoke.py` and `scripts/api_sweep.py`. If any step after the image push fails, traffic returns to the previous revision.
- Manual rollback: `gcloud run services update-traffic support-agent --region REGION --to-revisions REVISION=100`. Migrations are forward-only and additive; never roll the schema back in production.
- Rollback drill (quarterly, on staging): deploy, note the previous revision, break `/health` deliberately on a branch, confirm the pipeline reverts traffic, then confirm `curl $URL/health` returns 200.

## Auto-scaling (Section 2: Speed & Scaling)
- API service (`support-agent`, deploy.yml "Deploy API service" step): `--min-instances 0 --max-instances 5`, default concurrency (80 concurrent requests per instance) and default resources (1 CPU, 512Mi memory) - none of the three are set explicitly in deploy.yml today. `min-instances 0` means the service scales to zero when idle: the first request after an idle period pays a cold-start (container start + app import), which shows up as a one-off slow request rather than a steady-state problem.
- Worker (`support-agent-worker`): a Cloud Run **Job**, not a service, triggered on a fixed one-minute schedule (Cloud Scheduler `support-agent-worker-tick`) with `--parallelism 1 --tasks 1`. It isn't autoscaled by load at all - it is already event/schedule-driven, runs at most 55s, and the real-time path (WhatsApp replies) bypasses it entirely via inline drain (see `app/webhooks/whatsapp.py`'s `_drain_now`, noted in deploy.yml). There is nothing to tune here for scaling; the one-minute tick is a safety net, not the hot path.
- When to revisit `max-instances 5`: each instance can hold up to 80 concurrent requests, so 5 instances is headroom for 400 concurrent requests, which comfortably covers the current multi-tenant load. Raise it (`--max-instances N` on the same `gcloud run deploy` line) once the client/webhook count grows enough that Admin Control Tower's traffic numbers (`/admin/monitoring/traffic`) show sustained concurrency near that ceiling - watching that dashboard is the trigger, not a calendar date.
- When to set `min-instances` above 0: only once cold-start latency on the first request after idle periods is actually reported as a problem (e.g. a webhook provider's own timeout is tight enough that a cold start risks a dropped delivery). `min-instances 1` removes cold starts entirely but bills one instance continuously - it is a latency-vs-cost trade-off to make deliberately, not a default to flip speculatively.
- `--concurrency` and `--cpu`/`--memory` are unset today (Cloud Run defaults: 80, 1 CPU/512Mi). These are the next levers if a specific symptom shows up: lower `--concurrency` if requests start queueing behind CPU-bound work on an instance (visible as rising p95 latency at the same traffic level rather than at higher traffic), raise `--memory` if a Cloud Run revision starts getting OOM-killed (visible in Cloud Run revision logs, not in the app's own error_id logs).

## Emergency stop
- All brands: Admin, Ops screen, "Sab brands ka agent roken" (or `POST /admin/global-pause` with a reason). Customer sends stop immediately; human replies still work. Resume with `POST /admin/global-resume`.
- One brand: Admin, Brands, pause the brand, or the brand owner presses pause on the dashboard.
- Mode back to shadow: Admin, Brands, mode.

## Alerts and first response
| Alert | Meaning | First step |
|---|---|---|
| API 5xx | errors from the API | Cloud Run logs filtered by `error_id`; roll back if it started with a deploy |
| Worker job failed | a job exhausted retries | Admin, Ops, jobs with `needs_review`; fix cause, then re-enqueue |
| Worker silent | scheduler or job broken | check the Scheduler job and last executions of `support-agent-worker` |
| LLM dheere chal raha hai | p95 above `LLM_P95_TARGET_MS` | provider status; messages still fall back to a human handoff |
| Kuch customers ko jawab nahi gaya | inbound jobs stuck or deferred | check `llm_calls` outcomes; check provider key and quota |
| Handoff SLA nikal gaya | a human has not picked up | brand owner should open Handoffs |
| Privacy request ki deadline | request near 30 days | owner opens Privacy, completes export or delete |

## Backup and restore
- Neon keeps point-in-time history. To restore: create a branch from the wanted timestamp, point `database-url` and `migration-database-url` at it, redeploy, verify with `scripts/smoke.py`, then promote.
- Restore drill (quarterly): restore to a new branch, run `alembic current` (must be head), run smoke, delete the branch. Record the date in PROGRESS.md.
- Data deleted by retention or a privacy delete is gone from the live database; it remains only in provider backups until they expire.

## Retention and privacy
- Daily `retention` job per brand: conversations older than `RETENTION_DAYS` (30) are deleted with their messages, runs, actions and handoffs (open handoffs are kept). External call logs and finished jobs older than `TECHNICAL_RETENTION_DAYS` (14) are removed, as are expired auth tokens and rate-limit rows.
- Legal hold: `UPDATE tenants SET legal_hold = true WHERE id = ...` through the owner role stops both retention and privacy deletes for that brand. Record why in the audit log.
- Privacy delete requests run in the same daily job and erase the customer's conversations, events and opt-out rows. Consent rows are append-only and keep only an internal id.
- Customer export: owner Privacy screen, "Export download karein".

## Logs and Sentry
- Logs are JSON with request_id and run_id. Message bodies and contact details are masked. Sentry has `send_default_pii=False`, strips request bodies, headers, cookies and local variables.

## V1: write actions (cancel, address, exchange, refund)
- Levels per tool: `off`, `approval`, `auto`. New brands start `off` or `approval`. `auto` is set only by the platform admin: Admin, Brands, Autonomy (`PUT /admin/tenants/{id}/autonomy`, reason required, brand must be in mode `auto`). Every change is a new policy version and an `admin.autonomy_changed` audit row.
- Every write: customer confirms ("haan"), the gate checks policy and limits, then claim, Shopify write, order re-read. The customer hears "ho gaya" only for `confirmed` actions.
- Approvals: owner opens Approvals. `pending_approval` older than 60 min shows the SLA chip and raises a notification (checked every 10 min, repeated at most every 4 h). Approve re-reads the order; if it changed the action is rejected as ORDER_CHANGED.
- `needs_review` (timeout, 5xx, partial refund): the money may or may not have moved. Open Approvals, check the order in Shopify, then press "Dobara check karein" (reconcile). Never re-run a write by hand; the idempotency key protects the retry.
- Anomaly stop: more than 10 auto actions of one tool in 10 minutes switches that tool off and notifies the owner. Find the cause, then restore the tool (Approvals, kill-switch panel).

## V1: rollout order and rollback levers
Order (TDD V1 section 11): address_update and order_cancel, then exchange_request, then refund_create. Per tool: shadow (review 100+ proposals, precision 99% or more), then `approval`, then `auto`. Gates are in `docs/RELEASE_GATES_V1.md`; run `python -m app.evals.run` before every autonomy change.

Rollback levers, fastest first. Each one is exercised by `tests/v1/test_rollback_drill.py`:
1. Global pause (Admin, Ops): all brands stop answering and writing.
2. Brand pause or mode back to `draft`/`shadow`: no new write actions for that brand.
3. Kill switch for one tool (Approvals, owner): queued confirmations of that tool stop at execute.
4. Admin sets the tool to `approval` or `off` (new policy version): the next customer confirmation waits for a human, or hands off.
Rolling back code is the Cloud Run traffic switch above. Migrations 0009 to 0012 are additive; do not drop them.

## Voice (calling agent)
- Setup: owner Dashboard > Calling me number (+91...) aur optional team ka number daalein; webhook link sirf ek baar dikhta hai, provider ke "call flow" me daalein. Link kho jaye to "Naya webhook link banayein".
- Provider flow: har bolne wale turn par POST (event=turn, speech, confidence), jawab ka `say` TTS se bolen; `action=transfer` par `transfer_to` par dial, `hangup` par kaat dein.
- Agent tab bolta hai jab mode=auto, pause nahi, policy V2. Warna transfer (office hours me) ya callback.
- Rokna: tenant ko pause karein ya Calling "chalu" band karein; naye call turant human/callback par jayenge.
- Callback chahiye wale calls: Handoffs me reason dekhein. "Customer ko call karke batayein" notification ka matlab call ke baad action approve hua; customer ko phone karke batayein.
- Transcript Voice page me; audio store nahi hota. Har call par "Sahi tha / Galat tha" dabayein; 7-din ke stats (agent ne khud kitne sambhale, callback, average jawab ms, dheemi baatein) usi page par.
- Provider badalna: Calling page par "Telephony provider" chunein. Naye provider ke liye `app/voice/providers.py` me ek adapter class (parse + render) jodna padta hai.
- Transfer lag nahi paya: provider `end` event me reason `no_answer`/`busy`/`failed` bheje. Tab call callback ban jaati hai, handoff urgent hota hai aur alert jaata hai.
- Call summary WhatsApp par tabhi jaati hai jab customer ne pichle 24 ghante me WhatsApp kiya ho; warna "skipped".
- Latency `VOICE_SLOW_MS` (default 2000) se zyada ho to "dheemi baat" ginti hai. Dheemi baatein badhein to LLM provider aur DB check karein.

## Drills (record the date in PROGRESS.md)
- Quarterly: deploy rollback drill (above) and restore drill (Backup and restore).
- Before each tool goes `auto`: run the four levers on staging with the fake writer and confirm that each one stops a pending write.
- Not yet run on real infrastructure: Shopify write API, Gupshup callback, LLM accuracy. They are VERIFY items in DECISIONS.md and block `auto` for any brand until checked.
