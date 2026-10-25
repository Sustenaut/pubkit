# Runs without credentials: every provider is mocked. testdata/pubkit.json
# has three zones: example.eu on Hetzner and Cloudflare, with mailbox.org
# email, a server at the apex, a Pages site and a Cloud Run site;
# example.org on Hetzner alone, with a server and a Pages site served from
# its standby server; and example.net on deSEC and Cloudflare, signed by
# both, with a server at the apex. Assertions read the providers' facts from var.config,
# the same file, so none are written here.

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
      name_servers = ["ns1.cloudflare.example", "ns2.cloudflare.example"]
    }
  }
  mock_resource "cloudflare_zone_dnssec" {
    defaults = {
      ds = "example.net. 3600 IN DS 2371 13 2 B30B5BF3A16AA4C258BB336821564F033A9E53F034E0650210F4EB8901888AE8"
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
    condition = toset(keys(output.records["example.eu"])) == toset(concat(
      ["email_mx", "email_dmarc", "apex_txt", "apex_a", "apex_aaaa", "recipes", "pub"],
      [for selector in keys(var.config.providers.mailbox.dkim) : "email_dkim_${selector}"],
      [for name in keys(var.config.zones["example.eu"].email.verification) : "email_verification_${name}"],
    ))
    error_message = "example.eu: its mail provider's records, the apex TXT rrset and its three sites."
  }

  assert {
    condition = (
      tolist(output.records["example.eu"]["email_mx"].values) == tolist(var.config.providers.mailbox.mx) &&
      tolist(output.records["example.eu"]["apex_txt"].values) == tolist(concat(
        ["\"v=spf1 ${var.config.providers.mailbox.spf} ~all\""],
        [for value in var.config.zones["example.eu"].apex_txt : "\"${value}\""],
      )) &&
      tolist(output.records["example.eu"]["email_dmarc"].values) == tolist(["\"v=DMARC1; p=${var.config.zones["example.eu"].email.dmarc.policy}; rua=mailto:${var.config.zones["example.eu"].email.dmarc.rua}\""]) &&
      alltrue([
        for selector, target in var.config.providers.mailbox.dkim :
        output.records["example.eu"]["email_dkim_${selector}"].name == "${selector}._domainkey" &&
        tolist(output.records["example.eu"]["email_dkim_${selector}"].values) == tolist([target])
      ]) &&
      alltrue([
        for name, value in var.config.zones["example.eu"].email.verification :
        output.records["example.eu"]["email_verification_${name}"].name == name &&
        tolist(output.records["example.eu"]["email_verification_${name}"].values) == tolist(["\"${value}\""])
      ])
    )
    error_message = "The email records come from the mailbox-org provider and the zone's email block."
  }

  assert {
    condition = (
      output.records["example.eu"]["apex_a"].name == "@" &&
      tolist(output.records["example.eu"]["apex_aaaa"].values) == tolist(["2001:db8::1"]) &&
      output.records["example.eu"]["recipes"].type == "CNAME" &&
      tolist(output.records["example.eu"]["recipes"].values) == tolist(["example-recipes-2ab.pages.dev."]) &&
      tolist(output.records["example.eu"]["pub"].values) == tolist([var.config.providers.gcp.cloud_run.domain_target])
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
      output.nameservers["example.eu"] == sort(concat(var.config.providers.hetzner.dns.external_nameservers, ["ns1.cloudflare.example.", "ns2.cloudflare.example."])) &&
      output.nameservers["example.org"] == sort(var.config.providers.hetzner.dns.external_nameservers)
    )
    error_message = "The registrar gets the external nameservers from pubkit.json, and Cloudflare's where it serves the zone too."
  }

  assert {
    condition     = output.cloudflare_zone_ids["example.eu"] == "0123456789abcdef0123456789abcdef" && output.cloudflare_zone_ids["example.org"] == null
    error_message = "example.org is not at Cloudflare."
  }

  assert {
    condition = (
      output.nameservers["example.net"] == sort(concat(var.config.providers.desec.dns.external_nameservers, ["ns1.cloudflare.example.", "ns2.cloudflare.example."])) &&
      output.apex_nameservers["example.net"].values == output.nameservers["example.net"] &&
      output.apex_nameservers["example.org"] == null
    )
    error_message = "example.net: deSEC's nameservers and Cloudflare's, for the registrar and in the apex NS rrset, which deSEC takes too."
  }

  assert {
    condition = (
      module.zone["example.net"].cloudflare_ds == "2371 13 2 B30B5BF3A16AA4C258BB336821564F033A9E53F034E0650210F4EB8901888AE8" &&
      output.cloudflare_ds["example.net"] == module.zone["example.net"].cloudflare_ds &&
      output.cloudflare_ds["example.eu"] == null
    )
    error_message = "Only example.net is signed, and its DS record at Cloudflare is an output."
  }

  assert {
    condition     = toset(keys(output.records["example.net"])) == toset(["apex_a"])
    error_message = "example.net's records are its server site's; deSEC writes them in terraform/desec-zones."
  }

  assert {
    condition = (
      cloudflare_pages_project.site["recipes.example.eu"].account_id == var.config.providers.cloudflare.account_id &&
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

# testdata/pubkit.json sets the email TTL for every zone, the sites' TTL
# for example.org, and docs.example.org's own; the rest are the defaults.
run "ttls" {
  command = plan

  assert {
    condition = (
      output.records["example.eu"]["email_mx"].ttl == var.config.ttl.email &&
      output.records["example.eu"]["apex_txt"].ttl == var.config.ttl.email &&
      output.records["example.eu"]["apex_a"].ttl == 300 &&
      output.records["example.org"]["auth_a"].ttl == var.config.zones["example.org"].ttl.sites &&
      output.records["example.org"]["docs_a"].ttl == var.config.zones["example.org"].sites.docs.ttl
    )
    error_message = "A site's TTL wins over its zone's, a zone's over hosting.ttl, and hosting.ttl over the default."
  }
}

run "ttl_in_range" {
  command = plan

  variables {
    config = merge(jsondecode(file("../testdata/pubkit.json")).hosting, { ttl = { sites = 5 } })
  }

  expect_failures = [var.config]
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
      providers = { gcp = jsondecode(file("../testdata/pubkit.json")).hosting.providers.gcp }
      zones     = { "example.eu" = { dns = ["gcp"] } }
    }
  }

  expect_failures = [var.config]
}

run "site_kind_follows_its_provider" {
  command = plan

  variables {
    config = {
      providers = jsondecode(file("../testdata/pubkit.json")).hosting.providers
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

run "dnssec_keys_follow_dns" {
  command = plan

  variables {
    config = merge(jsondecode(file("../testdata/pubkit.json")).hosting, {
      zones = {
        "example.net" = {
          dns    = ["desec"]
          dnssec = jsondecode(file("../testdata/pubkit.json")).hosting.zones["example.net"].dnssec
        }
      }
    })
  }

  expect_failures = [var.config]
}
