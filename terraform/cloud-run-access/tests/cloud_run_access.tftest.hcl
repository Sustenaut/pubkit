# Runs without credentials: Google is mocked. The project and region are
# testdata/pubkit.json's, as a repository reads its own.

mock_provider "google" {}

variables {
  project = jsondecode(file("../testdata/pubkit.json")).hosting.providers.gcp.project
  region  = jsondecode(file("../testdata/pubkit.json")).hosting.providers.gcp.region
  services = {
    pub-site  = ["user:a@example.eu", "user:b@example.eu"]
    blog-site = ["user:a@example.eu"]
  }
}

run "one_grant_per_person_and_service" {
  command = apply

  assert {
    condition = toset(keys(google_iap_web_cloud_run_service_iam_member.accessor)) == toset([
      "pub-site user:a@example.eu", "pub-site user:b@example.eu", "blog-site user:a@example.eu",
    ])
    error_message = "Each member gets each of their services."
  }

  assert {
    condition = (
      google_iap_web_cloud_run_service_iam_member.accessor["pub-site user:b@example.eu"].cloud_run_service_name == "pub-site" &&
      google_iap_web_cloud_run_service_iam_member.accessor["pub-site user:b@example.eu"].role == "roles/iap.httpsResourceAccessor" &&
      google_iap_web_cloud_run_service_iam_member.accessor["pub-site user:b@example.eu"].location == "europe-north1"
    )
    error_message = "A grant names its service, in the provider's region."
  }
}
