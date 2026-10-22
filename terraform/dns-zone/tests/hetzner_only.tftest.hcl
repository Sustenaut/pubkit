# Hetzner alone, with the real Cloudflare provider left unconfigured: a root
# module whose zones Cloudflare does not serve needs no Cloudflare token or
# provider block.

mock_provider "hcloud" {
  mock_data "hcloud_zone" {
    defaults = {
      name                      = "example.eu"
      authoritative_nameservers = { assigned = ["ns1.assigned.example.com."] }
    }
  }
}

variables {
  zone    = "example.eu"
  hetzner = jsondecode(file("../testdata/pubkit.json")).hosting.providers.hetzner
  records = { web = { name = "@", type = "A", ttl = 300, values = ["192.0.2.1"] } }
}

run "no_cloudflare_configuration" {
  command = plan

  assert {
    condition     = length(hcloud_zone_rrset.records) == 1 && output.cloudflare_zone_id == null
    error_message = "Hetzner alone plans without a Cloudflare token."
  }

  assert {
    condition     = output.nameservers == sort([for ns in var.hetzner.dns.external_nameservers : "${trimsuffix(ns, ".")}."])
    error_message = "The registrar gets the external nameservers given."
  }
}
