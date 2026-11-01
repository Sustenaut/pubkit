# Runs without credentials: Google is mocked. The project and region are
# testdata/pubkit.json's, as a repository reads its own.

mock_provider "google" {
  mock_resource "google_service_account" {
    defaults = {
      name   = "projects/example-project/serviceAccounts/mocked-account@example-project.iam.gserviceaccount.com"
      email  = "mocked-account@example-project.iam.gserviceaccount.com"
      member = "serviceAccount:mocked-account@example-project.iam.gserviceaccount.com"
    }
  }
}

variables {
  name                 = "pub-site"
  project              = jsondecode(file("../testdata/pubkit.json")).hosting.providers.gcp.project
  project_number       = "123456789"
  region               = jsondecode(file("../testdata/pubkit.json")).hosting.providers.gcp.region
  account_id           = "example-pub-site"
  account_display_name = "Example pub-site"
}

run "a_service_behind_iap" {
  command = apply

  assert {
    condition = (
      google_cloud_run_v2_service.site.name == "pub-site" &&
      google_cloud_run_v2_service.site.iap_enabled &&
      google_cloud_run_v2_service.site.deletion_protection &&
      google_cloud_run_v2_service.site.template[0].containers[0].image == "us-docker.pkg.dev/cloudrun/container/hello"
    )
    error_message = "The service starts on the placeholder, behind IAP, protected from deletion."
  }

  assert {
    condition     = google_cloud_run_v2_service_iam_member.iap_invoker[0].member == "serviceAccount:service-123456789@gcp-sa-iap.iam.gserviceaccount.com"
    error_message = "IAP's service agent may call the service."
  }

  assert {
    condition = (
      google_artifact_registry_repository.site.repository_id == "pub-site" &&
      length(google_artifact_registry_repository.site.cleanup_policies) == 2 &&
      output.image_repository == "europe-north1-docker.pkg.dev/example-project/pub-site"
    )
    error_message = "The registry is named for the service and cleans up after itself."
  }

  assert {
    condition = (
      length(google_service_account.deploy) == 0 &&
      length(google_service_account_iam_member.deploy_from_github) == 0 &&
      output.deploy_account == null
    )
    error_message = "No repository, no deploy account."
  }
}

run "without_iap" {
  command = apply

  variables {
    iap = false
  }

  assert {
    condition     = !google_cloud_run_v2_service.site.iap_enabled && length(google_cloud_run_v2_service_iam_member.iap_invoker) == 0
    error_message = "Without IAP, nothing is set up for it."
  }
}

run "deploys_from_one_repository" {
  command = apply

  variables {
    deploy_repository = "example-org/pub-site"
  }

  assert {
    condition     = google_service_account.deploy[0].account_id == "example-pub-site-deploy"
    error_message = "The deploy account's id is the site's with -deploy."
  }

  assert {
    condition     = google_service_account_iam_member.deploy_from_github[0].member == "principalSet://iam.googleapis.com/projects/123456789/locations/global/workloadIdentityPools/github/attribute.repository/example-org/pub-site"
    error_message = "Only that repository, through the pool, can act as the deploy account."
  }

  assert {
    condition = (
      google_artifact_registry_repository_iam_member.deploy_push[0].role == "roles/artifactregistry.writer" &&
      google_cloud_run_v2_service_iam_member.deploy[0].role == "roles/run.developer" &&
      google_service_account_iam_member.deploy_acts_as_site[0].role == "roles/iam.serviceAccountUser"
    )
    error_message = "It can push to this registry, deploy this service and run it as its account."
  }
}

run "an_env_entry_is_a_value_or_a_secret" {
  command = apply

  variables {
    env = [
      { name = "PLAIN", value = "a" },
      { name = "KEY", secret = "some-key" },
    ]
  }

  assert {
    condition = (
      one([for e in google_cloud_run_v2_service.site.template[0].containers[0].env : e.value if e.name == "PLAIN"]) == "a" &&
      one(flatten([for e in google_cloud_run_v2_service.site.template[0].containers[0].env : [for v in e.value_source : [for r in v.secret_key_ref : "${r.secret}:${r.version}"]] if e.name == "KEY"])) == "some-key:latest"
    )
    error_message = "An entry is a value or a secret's latest version."
  }
}

run "an_env_entry_is_one_or_the_other" {
  command = plan

  variables {
    env = [{ name = "BOTH", value = "a", secret = "b" }]
  }

  expect_failures = [var.env]
}
