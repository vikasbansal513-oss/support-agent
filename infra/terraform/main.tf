# ---------------------------------------------------------------------------
# This file codifies RUNBOOK.md's "One-time setup" steps 1-3, which until now were
# manual gcloud/console steps nobody could reproduce from the repo. If you are applying
# this for the FIRST time against infrastructure that already exists (it almost
# certainly does - see README.md in this folder), `terraform import` every resource
# below before your first `terraform apply`, or Terraform will try to create duplicates
# and fail on "already exists" errors.
# ---------------------------------------------------------------------------

# --- Artifact Registry (RUNBOOK step 1) -------------------------------------------
resource "google_artifact_registry_repository" "support_agent" {
  location      = var.region
  repository_id = var.artifact_repo_name
  format        = "DOCKER"
  description   = "support-agent backend container images (built by .github/workflows/deploy.yml)"
}

# --- Service accounts (RUNBOOK step 1) ---------------------------------------------
resource "google_service_account" "runtime" {
  account_id   = "support-runtime"
  display_name = "support-agent Cloud Run runtime (API + worker + migrate jobs)"
}

resource "google_service_account" "deploy" {
  account_id   = "support-deployer"
  display_name = "support-agent deployer (GitHub Actions)"
}

resource "google_service_account" "scheduler" {
  account_id   = "support-scheduler"
  display_name = "support-agent scheduler"
}

# --- IAM (RUNBOOK step 3) -----------------------------------------------------------

# Runtime SA: secretmanager.secretAccessor, scoped to only the secrets this app actually
# reads (var.secret_names) rather than project-wide - RUNBOOK's wording didn't specify a
# scope; per-secret is the tighter, equally-functional choice.
resource "google_secret_manager_secret_iam_member" "runtime_secret_access" {
  for_each  = toset(var.secret_names)
  secret_id = google_secret_manager_secret.app[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

# Scheduler SA: run.invoker on the worker JOB specifically (not project-wide) - this is
# the only thing it ever needs to do.
resource "google_cloud_run_v2_job_iam_member" "scheduler_invokes_worker" {
  name     = "support-agent-worker"
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"

  # The job itself is created by deploy.yml's `gcloud run jobs deploy`, not by Terraform
  # (see README.md in this folder for why) - this IAM binding still works once that job
  # exists, but `terraform apply` before the first deploy.yml run will fail with
  # "job not found." Deploy once via deploy.yml, then apply this binding.
}

# Deploy SA: the 4 project-level roles RUNBOOK step 3 lists.
resource "google_project_iam_member" "deploy_run_admin" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${google_service_account.deploy.email}"
}

resource "google_project_iam_member" "deploy_artifact_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.deploy.email}"
}

resource "google_project_iam_member" "deploy_scheduler_admin" {
  project = var.project_id
  role    = "roles/cloudscheduler.admin"
  member  = "serviceAccount:${google_service_account.deploy.email}"
}

# iam.serviceAccountUser scoped to just the runtime SA (deploy needs to deploy Cloud Run
# resources that run AS the runtime SA) - tighter than a project-wide grant of the same
# role, which would let the deploy SA impersonate every service account in the project.
resource "google_service_account_iam_member" "deploy_acts_as_runtime" {
  service_account_id = google_service_account.runtime.name
  role                = "roles/iam.serviceAccountUser"
  member              = "serviceAccount:${google_service_account.deploy.email}"
}

# --- Workload Identity Federation (RUNBOOK step 2) ----------------------------------
# Lets GitHub Actions impersonate the deploy SA with a short-lived OIDC token - no
# long-lived service account key ever leaves GCP. Matches google-github-actions/auth@v2's
# usage in deploy.yml (workload_identity_provider + service_account inputs).
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-pool"
  display_name              = "GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id         = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-provider"
  display_name                      = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }

  # Only this exact repo may mint a token under this provider - a Terraform-enforced
  # version of the trust boundary RUNBOOK step 2 only describes in prose.
  attribute_condition = "assertion.repository == \"${var.github_repo}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github_impersonates_deploy" {
  service_account_id = google_service_account.deploy.name
  role                = "roles/iam.workloadIdentityUser"
  member              = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}

# --- Secret Manager secret containers (names only - never values) ------------------
resource "google_secret_manager_secret" "app" {
  for_each  = toset(var.secret_names)
  secret_id = each.key

  replication {
    auto {}
  }
}

# --- Cloud Scheduler (RUNBOOK "Moving parts" + deploy.yml's "Scheduler" step) -------
# NOTE: deploy.yml's own `gcloud scheduler jobs update ... || gcloud scheduler jobs
# create ...` step ALREADY manages this job's schedule on every deploy. Declaring it
# here too is deliberate belt-and-suspenders for disaster recovery (see README.md): if
# the scheduler job is ever deleted outside of a deploy (console mistake, project
# migration), `terraform apply` recreates it with the right schedule without waiting for
# the next deploy. `lifecycle.ignore_changes` on schedule stops Terraform and deploy.yml
# from fighting each other if the cron is ever changed by one and not the other - the
# single source of truth for the cron value stays deploy.yml's "Scheduler" step; update
# it there, and update the comment there (not here) that a doc-vs-code audit will trust.
resource "google_cloud_scheduler_job" "worker_tick" {
  name      = "support-agent-worker-tick"
  region    = var.region
  schedule  = "0 * * * *" # hourly - keep in sync with deploy.yml's "Scheduler" step
  time_zone = "Etc/UTC"

  http_target {
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/support-agent-worker:run"
    http_method = "POST"
    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }

  lifecycle {
    ignore_changes = [schedule]
  }

  depends_on = [google_cloud_run_v2_job_iam_member.scheduler_invokes_worker]
}
