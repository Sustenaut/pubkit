# Cloudflare beside deSEC, signing the zone with it (multi-signer), and
# deSEC beside Hetzner and Cloudflare while a zone moves. deSEC's records
# are terraform/desec-zones'; here it is only its nameservers. The
# providers are mocked, with made-up nameservers and a made-up DS; the keys
# and deSEC's nameservers come from testdata/pubkit.json's example.net.

mock_provider "hcloud" {
  mock_data "hcloud_zone" {
    defaults = {
      name                      = "example.net"
      authoritative_nameservers = { assigned = ["ns1.assigned.example.com."] }
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
}

variables {
  zone       = "example.net"
  desec      = jsondecode(file("../testdata/pubkit.json")).hosting.providers.desec
  cloudflare = jsondecode(file("../testdata/pubkit.json")).hosting.providers.cloudflare
  dnssec     = jsondecode(file("../testdata/pubkit.json")).hosting.zones["example.net"].dnssec
  records = {
    web = { name = "@", type = "A", ttl = 300, values = ["192.0.2.1"] }
  }
}

run "signed_beside_desec" {
  command = apply

  assert {
    condition = (
      cloudflare_zone_dnssec.zone[0].status == "active" &&
      cloudflare_zone_dnssec.zone[0].dnssec_multi_signer &&
      cloudflare_zone_dnssec.zone[0].dnssec_use_nsec3 == null
    )
    error_message = "Cloudflare signs, in multi-signer mode, with denial of existence left to the plan."
  }

  assert {
    condition = (
      length(cloudflare_dns_record.dnskeys) == 1 &&
      one(values(cloudflare_dns_record.dnskeys)).data.flags == 257 &&
      one(values(cloudflare_dns_record.dnskeys)).data.protocol == 3 &&
      one(values(cloudflare_dns_record.dnskeys)).data.algorithm == 13 &&
      one(values(cloudflare_dns_record.dnskeys)).data.public_key == split(" ", var.dnssec.keys.desec[0])[3] &&
      one(values(cloudflare_dns_record.dnskeys)).ttl == 3600 &&
      one(values(cloudflare_dns_record.dnskeys)).name == "example.net"
    )
    error_message = "Cloudflare publishes deSEC's key, and only deSEC's, at the apex."
  }

  assert {
    condition = toset([for i in range(2) : cloudflare_dns_record.records["nameservers ${i}"].content]) == toset([
      "ns1.desec.io", "ns2.desec.org",
    ])
    error_message = "Cloudflare's NS records are deSEC's nameservers."
  }

  assert {
    condition     = cloudflare_zone_dns_settings.zone[0].multi_provider
    error_message = "Cloudflare runs in multi-provider mode beside deSEC."
  }

  assert {
    condition = output.apex_nameservers.ttl == 86400 && output.apex_nameservers.name == "@" && output.apex_nameservers.values == tolist([
      "ns1.cloudflare.example.", "ns1.desec.io.", "ns2.cloudflare.example.", "ns2.desec.org.",
    ])
    error_message = "The apex NS rrset, for deSEC too, lists Cloudflare's nameservers and deSEC's."
  }

  assert {
    condition     = output.nameservers == output.apex_nameservers.values
    error_message = "The registrar gets deSEC's nameservers and Cloudflare's."
  }

  assert {
    condition     = output.cloudflare_ds == "2371 13 2 B30B5BF3A16AA4C258BB336821564F033A9E53F034E0650210F4EB8901888AE8"
    error_message = "Output cloudflare_ds is Cloudflare's DS record for the registrar."
  }
}

# Without dnssec nothing signs at Cloudflare and no keys are published.
run "unsigned" {
  command = apply

  variables {
    dnssec = null
  }

  assert {
    condition     = length(cloudflare_zone_dnssec.zone) == 0 && length(cloudflare_dns_record.dnskeys) == 0
    error_message = "No signing at Cloudflare and no published keys without dnssec."
  }

  assert {
    condition     = output.cloudflare_ds == null
    error_message = "No DS record without dnssec."
  }
}

# Step 1 of a move: deSEC as a third copy beside Hetzner and Cloudflare,
# unsigned. Each provider's apex NS rrset lists all three.
run "three_providers" {
  command = apply

  variables {
    hetzner = {}
    dnssec  = null
  }

  assert {
    condition = tolist([for r in hcloud_zone_rrset.records["nameservers"].records : r.value]) == sort([
      "ns1.assigned.example.com.", "ns1.cloudflare.example.", "ns2.cloudflare.example.", "ns1.desec.io.", "ns2.desec.org.",
    ])
    error_message = "Hetzner's apex NS rrset lists every provider's nameservers."
  }

  assert {
    condition     = toset(output.apex_nameservers.values) == toset(hcloud_zone_rrset.records["nameservers"].records[*].value)
    error_message = "The apex NS rrset for deSEC is Hetzner's."
  }

  assert {
    condition = toset([for i in range(3) : cloudflare_dns_record.records["nameservers ${i}"].content]) == toset([
      "ns1.assigned.example.com", "ns1.desec.io", "ns2.desec.org",
    ])
    error_message = "Cloudflare's NS records are Hetzner's and deSEC's."
  }

  assert {
    condition = output.nameservers == sort([
      "ns1.assigned.example.com.", "ns1.cloudflare.example.", "ns2.cloudflare.example.", "ns1.desec.io.", "ns2.desec.org.",
    ])
    error_message = "The registrar gets every provider's nameservers."
  }
}

# deSEC alone: nothing here but its nameservers for the registrar.
run "desec_alone" {
  command = plan

  variables {
    cloudflare = null
    dnssec     = null
  }

  assert {
    condition     = output.apex_nameservers == null && output.nameservers == tolist(["ns1.desec.io.", "ns2.desec.org."])
    error_message = "One provider: no apex NS rrset, and the registrar gets deSEC's nameservers."
  }
}

run "hetzner_cannot_sign" {
  command = plan

  variables {
    hetzner = {}
  }

  expect_failures = [var.dnssec]
}

run "keys_for_each_provider" {
  command = plan

  variables {
    dnssec = { keys = { desec = ["257 3 13 AAAA"] } }
  }

  expect_failures = [var.dnssec]
}

run "keys_are_dnskey_values" {
  command = plan

  variables {
    dnssec = { keys = { desec = ["257 3 13 AAAA"], cloudflare = ["257 13 AAAA"] } }
  }

  expect_failures = [var.dnssec]
}

run "desec_needs_its_nameservers" {
  command = plan

  variables {
    desec = { dns = { external_nameservers = [] } }
  }

  expect_failures = [var.desec]
}
