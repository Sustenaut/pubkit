# Runs without credentials: every provider is mocked. testdata/pubkit.json
# has two zones: example.eu on Hetzner and Cloudflare, with mailbox.org's
# email, a server at the apex, a Pages site and a Cloud Run site; and
# example.org on Hetzner alone, with a server and a Pages site served from
# its standby server.

mock_provider "hcloud" {
  mock_data "hcloud_zone" {
    defaults = {
      authoritative_nameservers = {
        assigned = ["ns3.assigned.example.de.", "ns1.assigned.example.com", "ns2.assigned.example.com."]
      }
    }
  }
}

mock_provider "cloudflare" {
  mock_resource "cloudflare_zone" {
    defaults = {
      id           = "0123456789abcdef0123456789abcdef"
      name_servers = ["ada.ns.cloudflare.com", "bob.ns.cloudflare.com"]
    }
  }
  mock_resource "cloudflare_pages_project" {
    defaults = {
      subdomain = "example-recipes-2ab.pages.dev"
    }
  }
}

variables {
  config = jsondecode(file("../testdata/pubkit.json")).hosting
  servers = {
    web   = { ipv4 = "192.0.2.1", ipv6 = "2001:db8::1" }
    small = { ipv4 = "192.0.2.2" }
  }
}

run "zones" {
  command = apply

  assert {
    condition = toset(keys(output.records["example.eu"])) == toset([
      "email_mx", "email_dmarc", "email_dkim_mbo0001", "email_dkim_mbo0002", "email_dkim_mbo0003", "email_dkim_mbo0004",
      "email_verification_f67e29eb00a65673481c0f9731bc90e0c6f94fd3", "apex_txt",
      "apex_a", "apex_aaaa", "recipes", "pub",
    ])
    error_message = "example.eu: mailbox.org's records, the apex TXT rrset and its three sites."
  }

  assert {
    condition = (
      tolist(output.records["example.eu"]["email_mx"].values) == tolist(["10 mxext1.mailbox.org.", "10 mxext2.mailbox.org.", "10 mxext3.mailbox.org.", "10 mxext4.mailbox.org."]) &&
      tolist(output.records["example.eu"]["apex_txt"].values) == tolist(["\"v=spf1 include:mailbox.org ~all\"", "\"google-site-verification=abc\""]) &&
      tolist(output.records["example.eu"]["email_dmarc"].values) == tolist(["\"v=DMARC1; p=none; rua=mailto:alerts@example.eu\""]) &&
      output.records["example.eu"]["email_dkim_mbo0002"].name == "mbo0002._domainkey" &&
      tolist(output.records["example.eu"]["email_verification_f67e29eb00a65673481c0f9731bc90e0c6f94fd3"].values) == tolist(["\"0123456789abcdef0123456789abcdef01234567\""])
    )
    error_message = "The email records come from the mailbox-org provider and the zone's email block."
  }

  assert {
    condition = (
      output.records["example.eu"]["apex_a"].name == "@" &&
      tolist(output.records["example.eu"]["apex_aaaa"].values) == tolist(["2001:db8::1"]) &&
      output.records["example.eu"]["recipes"].type == "CNAME" &&
      tolist(output.records["example.eu"]["recipes"].values) == tolist(["example-recipes-2ab.pages.dev."]) &&
      tolist(output.records["example.eu"]["pub"].values) == tolist(["ghs.googlehosted.com."])
    )
    error_message = "A server site gets its addresses, a Pages site the subdomain Cloudflare reports, a Cloud Run site the provider's domain_target."
  }

  assert {
    condition     = toset(keys(output.records["example.org"])) == toset(["auth_a", "docs_a"])
    error_message = "example.org: no email, two sites on an IPv4-only server, and no AAAA."
  }

  assert {
    condition     = tolist(output.records["example.org"]["docs_a"].values) == tolist(["192.0.2.2"])
    error_message = "A Pages site served by its standby points at the standby's address."
  }

  assert {
    condition = (
      output.nameservers["example.eu"] == tolist(["ada.ns.cloudflare.com.", "bob.ns.cloudflare.com.", "helium.ns.hetzner.de.", "hydrogen.ns.hetzner.com.", "oxygen.ns.hetzner.com."]) &&
      output.nameservers["example.org"] == tolist(["helium.ns.hetzner.de.", "hydrogen.ns.hetzner.com.", "oxygen.ns.hetzner.com."])
    )
    error_message = "The registrar gets the external nameservers from pubkit.json, and Cloudflare's where it serves the zone too."
  }

  assert {
    condition     = output.cloudflare_zone_ids["example.eu"] == "0123456789abcdef0123456789abcdef" && output.cloudflare_zone_ids["example.org"] == null
    error_message = "Only example.eu is at Cloudflare."
  }

  assert {
    condition = (
      cloudflare_pages_project.site["recipes.example.eu"].account_id == "fedcba9876543210fedcba9876543210" &&
      cloudflare_pages_project.site["recipes.example.eu"].production_branch == "main" &&
      cloudflare_pages_domain.site["docs.example.org"].project_name == "example-docs"
    )
    error_message = "Each Pages site has its project, in the provider's account, and its domain."
  }

  assert {
    condition = (
      output.sites["recipes.example.eu"].project == "example-recipes" &&
      output.sites["example.eu"].url == "https://example.eu/" &&
      output.sites["pub.example.eu"].project == null
    )
    error_message = "The sites output names each site's project and address."
  }
}

run "extra_records" {
  command = plan

  variables {
    records = {
      "example.org" = { mail = { name = "@", type = "MX", ttl = 3600, values = ["10 mail.example.net."] } }
    }
  }

  assert {
    condition     = toset(keys(output.records["example.org"])) == toset(["auth_a", "docs_a", "mail"])
    error_message = "Records pubkit.json does not describe join their zone's."
  }
}

run "unknown_type" {
  command = plan

  variables {
    config = {
      providers = { aws = { type = "aws" } }
      zones     = {}
    }
  }

  expect_failures = [var.config]
}

run "dns_provider_must_serve_dns" {
  command = plan

  variables {
    config = {
      providers = { gcp = { type = "google-cloud", project = "p", region = "r" } }
      zones     = { "example.eu" = { dns = ["gcp"] } }
    }
  }

  expect_failures = [var.config]
}

run "site_kind_follows_its_provider" {
  command = plan

  variables {
    config = {
      providers = {
        hetzner    = { type = "hetzner" }
        cloudflare = { type = "cloudflare", account_id = "fedcba9876543210fedcba9876543210" }
      }
      zones = {
        "example.eu" = {
          dns   = ["hetzner"]
          sites = { www = { provider = "cloudflare", kind = "server", server = "web" } }
        }
      }
    }
  }

  expect_failures = [var.config]
}

run "server_needs_addresses" {
  command = plan

  variables {
    servers = { web = { ipv4 = "192.0.2.1" } }
  }

  expect_failures = [var.servers]
}
