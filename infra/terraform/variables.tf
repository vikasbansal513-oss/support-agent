variable "project_id" {
  description = "GCP project id (matches the GCP_PROJECT_ID GitHub Actions repo variable)."
  type        = string
}

variable "region" {
  description = "GCP region (matches the GCP_REGION GitHub Actions repo variable)."
  type        = string
  default     = "asia-south1"
}

variable "artifact_repo_name" {
  description = "Artifact Registry repository name (matches GCP_ARTIFACT_REPO)."
  type        = string
  default     = "support-agent"
}

variable "github_repo" {
  description = "GitHub repo allowed to federate via Workload Identity, as \"owner/repo\"."
  type        = string
}

variable "secret_names" {
  description = <<-EOT
    Secret Manager secret NAMES this app reads (see RUNBOOK.md "Secrets" and
    deploy.yml's APP_SECRETS/MIGRATION_DATABASE_URL). Terraform creates the secret
    containers only - never their values. Set each secret's actual value once, out of
    band, with `gcloud secrets versions add <name> --data-file=-` (or the console) -
    never `terraform apply` with a real secret in a .tfvars file.
  EOT
  type = list(string)
  default = [
    "database-url",
    "migration-database-url",
    "encryption-key",
    "jwt-secret",
    "admin-jwt-secret",
    "llm-api-key",
    "brevo-api-key",
    "google-client-id",
    "google-client-secret",
    "sentry-dsn",
  ]
}
