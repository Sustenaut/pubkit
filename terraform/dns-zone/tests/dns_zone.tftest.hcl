# Runs without credentials: both providers are mocked, with made-up
# nameservers in place of the ones each would report. The providers' own
# facts come from testdata/pubkit.json, as a root module's come from its
# pubkit.json; none are written here.

mock_provider "hcloud" {
  mock_data "hcloud_zone" {
    defaults = {
      name = "example.eu"
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
}

variables {
  zone    = "example.eu"
  hetzner = {}
  records = {
    web = { name = "@", type = "A", ttl = 300, values = ["192.0.2.1"] }
    mx = {
      name   = "@"
      type   = "MX"
      ttl    = 3600
      values = ["10 mxext1.example.org.", "20 mxext2.example.org."]
    }
    spf  = { name = "@", type = "TXT", ttl = 3600, values = ["\"v=spf1 -all\""] }
    docs = { name = "docs", type = "CNAME", ttl = 300, values = ["docs.example.org."] }
  }
}

run "hetzner_alone" {
  command = plan

  assert {
    condition     = length(hcloud_zone_rrset.records) == 4
    error_message = "Each record is one Hetzner rrset, and the apex NS rrset is left alone."
  }

  assert {
    condition     = tolist([for r in hcloud_zone_rrset.records["mx"].records : r.value]) == tolist(["10 mxext1.example.org.", "20 mxext2.example.org."])
    error_message = "Hetzner takes the values in zone-file form."
  }

  assert {
    condition     = length(cloudflare_zone.zone) == 0 && length(cloudflare_dns_record.records) == 0
    error_message = "No Cloudflare resources without var.cloudflare."
  }

  assert {
    condition     = output.nameservers == tolist(["ns1.assigned.example.com.", "ns2.assigned.example.com.", "ns3.assigned.example.de."])
    error_message = "Without Cloudflare or external nameservers the registrar gets the assigned ones, with trailing dots."
  }

  assert {
    condition     = output.cloudflare_zone_id == null
    error_message = "No Cloudflare zone id without Cloudflare."
  }
}

run "multi_provider" {
  command = apply

  variables {
    hetzner    = jsondecode(file("../testdata/pubkit.json")).hosting.providers.hetzner
    cloudflare = jsondecode(file("../testdata/pubkit.json")).hosting.providers.cloudflare
  }

  assert {
    condition     = cloudflare_zone.zone[0].type == "full" && cloudflare_zone_dns_settings.zone[0].multi_provider
    error_message = "Cloudflare serves the full zone, with multi-provider DNS on."
  }

  assert {
    condition = tolist([for r in hcloud_zone_rrset.records["nameservers"].records : r.value]) == sort([
      "ns1.assigned.example.com.", "ns2.assigned.example.com.", "ns3.assigned.example.de.", "ns1.cloudflare.example.", "ns2.cloudflare.example.",
    ])
    error_message = "Hetzner's apex NS rrset lists its assigned nameservers and Cloudflare's."
  }

  assert {
    condition     = tolist([for i in range(3) : cloudflare_dns_record.records["nameservers ${i}"].content]) == tolist(["ns1.assigned.example.com", "ns2.assigned.example.com", "ns3.assigned.example.de"])
    error_message = "Cloudflare's NS records are Hetzner's nameservers alone, without trailing dots."
  }

  assert {
    condition = (
      cloudflare_dns_record.records["mx 0"].priority == 10 &&
      cloudflare_dns_record.records["mx 0"].content == "mxext1.example.org" &&
      cloudflare_dns_record.records["mx 1"].priority == 20 &&
      cloudflare_dns_record.records["mx 1"].content == "mxext2.example.org" &&
      cloudflare_dns_record.records["mx 1"].name == "example.eu"
    )
    error_message = "MX values are split into priority and host, at the apex's full name."
  }

  assert {
    condition = (
      cloudflare_dns_record.records["docs 0"].name == "docs.example.eu" &&
      cloudflare_dns_record.records["docs 0"].content == "docs.example.org" &&
      cloudflare_dns_record.records["docs 0"].priority == null &&
      cloudflare_dns_record.records["spf 0"].content == "\"v=spf1 -all\""
    )
    error_message = "Other records keep their value, less a trailing dot, at the full name."
  }

  assert {
    condition     = alltrue([for record in cloudflare_dns_record.records : !record.proxied])
    error_message = "No record is proxied."
  }

  assert {
    condition     = length(cloudflare_dns_record.records) == 1 + 2 + 1 + 1 + 3
    error_message = "One Cloudflare record per value, and one NS record per Hetzner nameserver."
  }

  assert {
    condition = output.nameservers == sort(concat(
      [for ns in var.hetzner.dns.external_nameservers : "${trimsuffix(ns, ".")}."],
      ["ns1.cloudflare.example.", "ns2.cloudflare.example."],
    ))
    error_message = "The registrar gets Hetzner's external nameservers, as given, and Cloudflare's."
  }

  assert {
    condition     = hcloud_zone_rrset.records["nameservers"].ttl == 86400
    error_message = "The apex NS rrset's TTL is a day unless nameservers_ttl says otherwise."
  }

  assert {
    condition     = output.cloudflare_zone_id == "0123456789abcdef0123456789abcdef"
    error_message = "The Cloudflare zone's id is an output."
  }
}

run "nameservers_key_is_reserved" {
  command = plan

  variables {
    records = {
      nameservers = { name = "@", type = "NS", ttl = 3600, values = ["ns1.example.org."] }
    }
  }

  expect_failures = [var.records]
}

run "one_rrset_per_name_and_type" {
  command = plan

  variables {
    records = {
      spf    = { name = "@", type = "TXT", ttl = 3600, values = ["\"v=spf1 -all\""] }
      google = { name = "@", type = "txt", ttl = 3600, values = ["\"google-site-verification=x\""] }
    }
  }

  expect_failures = [var.records]
}

# Without external nameservers, the registrar gets the zone's assigned
# ones, next to Cloudflare's.
run "no_external_nameservers" {
  command = apply

  variables {
    cloudflare = jsondecode(file("../testdata/pubkit.json")).hosting.providers.cloudflare
  }

  assert {
    condition = output.nameservers == sort([
      "ns1.assigned.example.com.", "ns2.assigned.example.com.", "ns3.assigned.example.de.", "ns1.cloudflare.example.", "ns2.cloudflare.example.",
    ])
    error_message = "The assigned nameservers and Cloudflare's."
  }
}

run "nameservers_ttl" {
  command = plan

  variables {
    hetzner         = jsondecode(file("../testdata/pubkit.json")).hosting.providers.hetzner
    cloudflare      = jsondecode(file("../testdata/pubkit.json")).hosting.providers.cloudflare
    nameservers_ttl = 3600
  }

  assert {
    condition     = hcloud_zone_rrset.records["nameservers"].ttl == 3600
    error_message = "nameservers_ttl sets the apex NS rrset's TTL."
  }
}

run "cloudflare_alone" {
  command = apply

  variables {
    hetzner    = null
    cloudflare = jsondecode(file("../testdata/pubkit.json")).hosting.providers.cloudflare
  }

  assert {
    condition     = length(cloudflare_dns_record.records) == 1 + 2 + 1 + 1 && length(cloudflare_zone_dns_settings.zone) == 0
    error_message = "Cloudflare alone: one record per value, no NS records, no multi-provider DNS."
  }

  assert {
    condition     = output.nameservers == tolist(["ns1.cloudflare.example.", "ns2.cloudflare.example."])
    error_message = "The registrar gets Cloudflare's nameservers."
  }
}

run "some_provider" {
  command = plan

  variables {
    hetzner = null
  }

  expect_failures = [var.cloudflare]
}
