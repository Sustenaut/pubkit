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
      hetzner = { type = "hetzner" }
      mailbox = { type = "mailbox-org", mx = ["10 mxext1.mailbox.org."], spf = "include:mailbox.org" }
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
    condition     = toset(keys(output.records["example.eu"])) == toset(["email_mx", "apex_txt", "auth_a"])
    error_message = "MX, SPF and the server's address, and nothing at Cloudflare."
  }

  assert {
    condition     = output.nameservers["example.eu"] == tolist(["ns1.assigned.example.com."])
    error_message = "Without external nameservers the registrar gets the assigned ones."
  }
}
