# What every Cloud Run site repeats: the account it runs as, its image
# registry, the service, and, when given a GitHub repository, an account only
# that repository can deploy with. The site's own buckets, secrets and any other
# roles are the repository's, beside this module. Who may open the site and its
# domain are not here: terraform/cloud-run-access and terraform/cloud-run-sites.
#
# Terraform owns the service's settings but not which image it runs: the first
# deploy replaces the placeholder, and Terraform leaves it alone after that.
#
# terraform_labels is ignored so that importing an existing service changes
# nothing: an imported resource has none in the state, so without this each would
# show an update to Terraform's own record of a label it already has.

locals {
  image_repository = "${var.region}-docker.pkg.dev/${var.project}/${google_artifact_registry_repository.site.repository_id}"
  deploy           = var.deploy_repository != null
  deploy_account   = coalesce(var.deploy_account_id, "${var.account_id}-deploy")
}

resource "google_service_account" "site" {
  project      = var.project
  account_id   = var.account_id
  display_name = var.account_display_name
  description  = var.account_description
}

resource "google_artifact_registry_repository" "site" {
  project       = var.project
  location      = var.region
  repository_id = var.name
  format        = "DOCKER"
  description   = var.registry_description

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }
  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      older_than = "2592000s" # 30 days, for anything beyond the ten kept above
    }
  }

  lifecycle {
    ignore_changes = [terraform_labels]
  }
}

resource "google_cloud_run_v2_service" "site" {
  project             = var.project
  name                = var.name
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  iap_enabled         = var.iap
  deletion_protection = true

  template {
    service_account                  = google_service_account.site.email
    timeout                          = var.timeout
    session_affinity                 = var.session_affinity
    max_instance_request_concurrency = var.concurrency

    scaling {
      min_instance_count = 0
      max_instance_count = var.max_instances
    }

    containers {
      image = "us-docker.pkg.dev/cloudrun/container/hello"
      ports {
        container_port = 8080
      }
      dynamic "env" {
        for_each = var.env
        content {
          name  = env.value.name
          value = env.value.value
          dynamic "value_source" {
            for_each = env.value.secret != null ? [env.value.secret] : []
            content {
              secret_key_ref {
                secret  = value_source.value
                version = "latest"
              }
            }
          }
        }
      }
      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
        cpu_idle = true
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version, terraform_labels]
  }
}

# IAP calls the service as its own service agent.
resource "google_cloud_run_v2_service_iam_member" "iap_invoker" {
  count = var.iap ? 1 : 0

  project  = var.project
  name     = google_cloud_run_v2_service.site.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:service-${var.project_number}@gcp-sa-iap.iam.gserviceaccount.com"
}

# --- Deploys from GitHub Actions, without keys --------------------------------

resource "google_service_account" "deploy" {
  count = local.deploy ? 1 : 0

  project      = var.project
  account_id   = local.deploy_account
  display_name = "${var.account_display_name} deploy"
  description  = "GitHub Actions in ${var.deploy_repository}: pushes ${var.name} images and deploys them to Cloud Run, and nothing else."
}

resource "google_service_account_iam_member" "deploy_from_github" {
  count = local.deploy ? 1 : 0

  service_account_id = google_service_account.deploy[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/projects/${var.project_number}/locations/global/workloadIdentityPools/${var.workload_identity_pool}/attribute.repository/${var.deploy_repository}"
}

resource "google_artifact_registry_repository_iam_member" "deploy_push" {
  count = local.deploy ? 1 : 0

  project    = var.project
  location   = var.region
  repository = google_artifact_registry_repository.site.name
  role       = "roles/artifactregistry.writer"
  member     = google_service_account.deploy[0].member
}

resource "google_cloud_run_v2_service_iam_member" "deploy" {
  count = local.deploy ? 1 : 0

  project  = var.project
  name     = google_cloud_run_v2_service.site.name
  location = var.region
  role     = "roles/run.developer"
  member   = google_service_account.deploy[0].member
}

# Deploying a revision that runs as the site's account needs actAs on it.
resource "google_service_account_iam_member" "deploy_acts_as_site" {
  count = local.deploy ? 1 : 0

  service_account_id = google_service_account.site.name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.deploy[0].member
}
