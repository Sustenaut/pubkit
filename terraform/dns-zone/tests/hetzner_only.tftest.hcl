# Hetzner alone, with the real Cloudflare provider left unconfigured: a root
# module without Cloudflare needs no Cloudflare token or provider block.

mock_provider "hcloud" {
  mock_data "hcloud_zone" {
    defaults = {
      name                      = "example.eu"
      authoritative_nameservers = { assigned = ["hydrogen.ns.hetzner.com."] }
    }
  }
}

variables {
  zone    = "example.eu"
  records = { web = { name = "@", type = "A", ttl = 300, values = ["192.0.2.1"] } }
}

run "no_cloudflare_configuration" {
  command = plan

  assert {
    condition     = length(hcloud_zone_rrset.records) == 1 && output.cloudflare_zone_id == null
    error_message = "Hetzner alone plans without a Cloudflare token."
  }
}
