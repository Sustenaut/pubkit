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
  bucket               = "example-backups"
  project              = jsondecode(file("../testdata/pubkit.json")).hosting.providers.gcp.project
  region               = jsondecode(file("../testdata/pubkit.json")).hosting.providers.gcp.region
  account_id           = "example-backup"
  account_display_name = "Example backups"
}

run "a_private_bucket_that_forgets" {
  command = apply

  assert {
    condition = (
      google_storage_bucket.backups.location == "europe-north1" &&
      google_storage_bucket.backups.public_access_prevention == "enforced" &&
      google_storage_bucket.backups.uniform_bucket_level_access &&
      !google_storage_bucket.backups.force_destroy
    )
    error_message = "The bucket is in the provider's region, private, and not destroyed with its contents."
  }

  assert {
    condition     = one(one(google_storage_bucket.backups.lifecycle_rule).condition).age == 30
    error_message = "Objects are deleted after 30 days by default."
  }
}

run "the_account_can_only_create_objects" {
  command = apply

  assert {
    condition     = google_storage_bucket_iam_member.writer.role == "roles/storage.objectCreator"
    error_message = "The writer can create objects, and neither read nor delete them."
  }

  assert {
    condition     = length(google_service_account_key.writer) == 1
    error_message = "The account has a key by default, for an app outside Google Cloud."
  }
}

run "no_key_when_not_asked_for" {
  command = apply

  variables {
    create_key     = false
    retention_days = 7
  }

  assert {
    condition     = length(google_service_account_key.writer) == 0 && output.key == null
    error_message = "create_key = false makes no key."
  }

  assert {
    condition     = one(one(google_storage_bucket.backups.lifecycle_rule).condition).age == 7
    error_message = "retention_days sets the age rule."
  }
}
