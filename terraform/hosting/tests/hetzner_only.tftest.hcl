# A repository on Hetzner alone, with the real Cloudflare provider left
# unconfigured: without sites or zones there, it needs no token or provider
# block for it. The module has no Google resources at all.

mock_provider "hcloud" {
  mock_data "hcloud_zone" {
    defaults = {
      authoritative_nameservers = { assigned = ["ns1.assigned.example.com."] }
    }
  }
}

variables {
  config = {
    providers = {
      hetzner = jsondecode(file("../testdata/pubkit.json")).hosting.providers.hetzner
      mailbox = jsondecode(file("../testdata/pubkit.json")).hosting.providers.mailbox
    }
    zones = {
      "example.eu" = {
        dns   = ["hetzner"]
        email = { provider = "mailbox" }
        sites = { auth = { provider = "hetzner", kind = "server", server = "k3s" } }
      }
    }
  }
  servers = { k3s = { ipv4 = "192.0.2.3" } }
}

run "no_cloudflare_configuration" {
  command = plan

  assert {
    condition = toset(keys(output.records["example.eu"])) == toset(concat(
      ["email_mx", "apex_txt", "auth_a"], [for selector in keys(var.config.providers.mailbox.dkim) : "email_dkim_${selector}"],
    ))
    error_message = "The mail provider's records and the server's address, and nothing at Cloudflare."
  }

  assert {
    condition     = output.nameservers["example.eu"] == sort(var.config.providers.hetzner.dns.external_nameservers)
    error_message = "The registrar gets the external nameservers from pubkit.json."
  }
}
