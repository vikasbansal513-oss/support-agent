terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # Remote state in GCS, not local state (local state is a single laptop's problem to
  # lose). Create this bucket by hand once, before the first `terraform init` - it can't
  # be managed by the config that needs it to store its own state. A reasonable name:
  # "<project-id>-tfstate", versioning ON, uniform bucket-level access ON.
  #
  #   gsutil mb -l <region> gs://<project-id>-tfstate
  #   gsutil versioning set on gs://<project-id>-tfstate
  #
  backend "gcs" {
    # bucket = "<project-id>-tfstate"   # fill in, or pass via -backend-config at init time
    prefix = "support-agent/state"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
