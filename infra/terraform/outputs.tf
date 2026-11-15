output "deploy_service_account_email" {
  value       = google_service_account.deploy.email
  description = "Set as the GCP_DEPLOY_SA GitHub Actions repo variable."
}

output "runtime_service_account_email" {
  value       = google_service_account.runtime.email
  description = "Set as the GCP_RUNTIME_SA GitHub Actions repo variable."
}

output "scheduler_service_account_email" {
  value       = google_service_account.scheduler.email
  description = "Set as the GCP_SCHEDULER_SA GitHub Actions repo variable."
}

output "workload_identity_provider" {
  value       = google_iam_workload_identity_pool_provider.github.name
  description = "Set as the GCP_WIF_PROVIDER GitHub Actions repo variable."
}

output "artifact_registry_repo" {
  value       = google_artifact_registry_repository.support_agent.repository_id
  description = "Set as the GCP_ARTIFACT_REPO GitHub Actions repo variable (should already match var.artifact_repo_name)."
}
