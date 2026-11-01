# Somewhere for an app's backups to go: a bucket that deletes objects past their
# retention, and an account that can create objects in it and nothing else, so a
# compromised app cannot delete or read its backups. The account's key is in the
# state; give it to the app outside Terraform.
#
# labels and terraform_labels are ignored once the bucket exists so that importing
# one changes nothing: an import reads neither, though the bucket has the label.

resource "google_storage_bucket" "backups" {
  project                     = var.project
  name                        = var.bucket
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  lifecycle_rule {
    condition {
      age = var.retention_days
    }
    action {
      type = "Delete"
    }
  }

  labels = { managed-by = "terraform" }

  lifecycle {
    ignore_changes = [labels, terraform_labels]
  }
}

resource "google_service_account" "writer" {
  project      = var.project
  account_id   = var.account_id
  display_name = var.account_display_name
  description  = var.account_description
}

resource "google_storage_bucket_iam_member" "writer" {
  bucket = google_storage_bucket.backups.name
  role   = "roles/storage.objectCreator"
  member = google_service_account.writer.member
}

resource "google_service_account_key" "writer" {
  count = var.create_key ? 1 : 0

  service_account_id = google_service_account.writer.name
}
