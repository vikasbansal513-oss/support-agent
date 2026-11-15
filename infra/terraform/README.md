# Terraform for support-agent's GCP infrastructure

## What this does and does not manage

**Manages (this folder):** Artifact Registry repo, the 3 service accounts, the IAM
bindings RUNBOOK.md step 3 used to describe only in prose, Workload Identity Federation
for GitHub Actions, Secret Manager secret *containers* (names only, never values), and
the Cloud Scheduler job as a disaster-recovery safety net.

**Does NOT manage, on purpose:** the Cloud Run service/job *images and revisions*
themselves (`.github/workflows/deploy.yml` keeps doing that on every release via
`gcloud run deploy`/`gcloud run jobs deploy` - that is deploy automation, a different
thing from the infrastructure shell it runs on), Neon Postgres (managed separately, not
a GCP resource), and secret *values* (set those with `gcloud secrets versions add`, never
in a `.tf`/`.tfvars` file).

## You almost certainly need to `terraform import` first, not `terraform apply`

This repo's RUNBOOK.md says the Artifact Registry repo, service accounts, and IAM
bindings were created once, by hand (`docs/RUNBOOK.md` "One-time setup" steps 1-3). If
that's still true for your project, **running `terraform apply` straight away will fail
with "already exists" errors**, or worse, create a second runtime service account with
the same intent but a different identity. Import each existing resource first:

```bash
terraform import google_artifact_registry_repository.support_agent \
  projects/<PROJECT_ID>/locations/<REGION>/repositories/support-agent

terraform import google_service_account.runtime \
  projects/<PROJECT_ID>/serviceAccounts/support-agent-runtime@<PROJECT_ID>.iam.gserviceaccount.com
terraform import google_service_account.deploy \
  projects/<PROJECT_ID>/serviceAccounts/support-agent-deploy@<PROJECT_ID>.iam.gserviceaccount.com
terraform import google_service_account.scheduler \
  projects/<PROJECT_ID>/serviceAccounts/support-agent-scheduler@<PROJECT_ID>.iam.gserviceaccount.com

# Repeat for each secret in var.secret_names:
terraform import 'google_secret_manager_secret.app["database-url"]' \
  projects/<PROJECT_ID>/secrets/database-url
# ... (migration-database-url, encryption-key, jwt-secret, admin-jwt-secret,
#      llm-api-key, brevo-api-key, google-client-id, google-client-secret, sentry-dsn)

terraform import google_cloud_scheduler_job.worker_tick \
  projects/<PROJECT_ID>/locations/<REGION>/jobs/support-agent-worker-tick
```

**Your existing service account names may not match** `support-agent-runtime` /
`support-agent-deploy` / `support-agent-scheduler` exactly, since RUNBOOK step 1 never
pinned down a naming convention. Check the actual names first:

```bash
gcloud iam service-accounts list --project <PROJECT_ID>
```

If yours differ, either rename them to match (safe - renaming the Terraform resource
address, not the real account, costs nothing) or edit `main.tf`'s `account_id` values to
match what already exists, then import against the real names.

**IAM bindings** (`google_secret_manager_secret_iam_member`,
`google_project_iam_member`, etc.) generally don't need importing the same way - running
`terraform apply` after the resources above are imported will show them as "to be
added," and adding a role binding that's functionally already granted (even if granted
by a different mechanism originally) is safe and idempotent.

**After import:** run `terraform plan`. A clean plan (no changes, or only additions you
expect) means the import worked. A plan that wants to *destroy and recreate* a service
account or the Artifact Registry repo means something doesn't match (wrong region, wrong
account id) - stop and reconcile before applying; destroying `support-agent-runtime`
would break the running production service.

## First-time setup (no existing resources - e.g. a fresh staging environment)

If you're setting up a genuinely new environment (the IaC gap this folder was built to
close - see the DevOps audit's Section 3), skip the import step and go straight to:

```bash
cd infra/terraform
terraform init -backend-config="bucket=<your-tfstate-bucket>"
terraform apply -var="project_id=<PROJECT_ID>" -var="github_repo=<owner>/<repo>"
```

Then take the 5 outputs (`terraform output`) and set them as the matching GitHub Actions
repo variables (`GCP_DEPLOY_SA`, `GCP_RUNTIME_SA`, `GCP_SCHEDULER_SA`,
`GCP_WIF_PROVIDER`, `GCP_ARTIFACT_REPO`), add the remaining repo variables RUNBOOK step 2
lists that aren't GCP-identity-related (`GCP_PROJECT_ID`, `GCP_REGION`, `FRONTEND_URL`,
`PUBLIC_BASE_URL`, `MAIL_FROM`, `GRIEVANCE_EMAIL`, `OPS_ALERT_EMAIL`, `LLM_MODEL`), fill
in the real secret values (`gcloud secrets versions add <name> --data-file=-` for each of
`var.secret_names`), then run `deploy.yml` with `confirm=DEPLOY` for the very first time
- that first run creates the Cloud Run service/jobs themselves (this Terraform config
deliberately doesn't).

## Why the Cloud Run service/jobs aren't in here

Terraform wants to fully own a resource's config; `deploy.yml` pushes a new container
image and (implicitly) a new revision on every single deploy. Having both manage the
same `google_cloud_run_v2_service` resource means every `terraform apply` either reverts
the latest deployed image back to whatever's in `main.tf` (bad) or constantly shows a
no-op diff that has to be `ignore_changes`-escaped field by field as deploy.yml's flags
evolve (fragile). Keeping deploy.yml as the single owner of the Cloud Run
service/job/migrate-job resources - and Terraform as the owner of everything around them
(identity, permissions, registry, scheduler) - is the cleaner boundary and needs no
constant hand-syncing between the two.
