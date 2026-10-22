# Runs without credentials: Google is mocked. testdata/pubkit.json has one
# Cloud Run site, pub.example.eu, among sites at other providers.

mock_provider "google" {}

variables {
  config = jsondecode(file("../testdata/pubkit.json")).hosting
}

run "mappings" {
  command = apply

  assert {
    condition     = toset(keys(google_cloud_run_domain_mapping.site)) == toset(["pub.example.eu"])
    error_message = "Only the Cloud Run site is mapped."
  }

  assert {
    condition = (
      google_cloud_run_domain_mapping.site["pub.example.eu"].location == var.config.providers.gcp.region &&
      google_cloud_run_domain_mapping.site["pub.example.eu"].metadata[0].namespace == var.config.providers.gcp.project &&
      google_cloud_run_domain_mapping.site["pub.example.eu"].spec[0].route_name == "pub-site"
    )
    error_message = "A Cloud Run site maps its name to its service, in the provider's project and region."
  }

  assert {
    condition     = output.domains["pub.example.eu"].service == "pub-site"
    error_message = "The domains output names each mapping's service."
  }
}
