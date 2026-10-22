# The domain mappings of pubkit.json's Cloud Run sites: each site's name,
# mapped to its service in its google-cloud provider's project and region.
# The service itself is the repository's own (its image, scaling and
# access); terraform/hosting writes the CNAME, and Google issues the
# certificate once it points at the provider's domain_target. Apart from
# terraform/hosting because the Google provider will not start without
# credentials, so only a repository on Google Cloud takes this one.

locals {
  sites = merge([
    for zone_name, zone in var.config.zones : {
      for name, site in zone.sites : "${name}.${zone_name}" => merge(site, {
        settings = var.config.providers[site.provider]
      }) if site.kind == "cloud-run"
    }
  ]...)
}

resource "google_cloud_run_domain_mapping" "site" {
  for_each = local.sites

  location = each.value.settings.region
  name     = each.key

  metadata {
    namespace = each.value.settings.project
  }
  spec {
    route_name = each.value.service
  }
}
