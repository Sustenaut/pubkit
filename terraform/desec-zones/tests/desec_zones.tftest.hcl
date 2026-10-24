# Runs without credentials: deSEC is mocked, with a made-up key and minimum
# TTL. testdata/pubkit.json has one zone at deSEC, example.net, beside
# Cloudflare and signed by both; records and apex_nameservers stand in for
# terraform/hosting's outputs.

mock_provider "desec" {
  mock_resource "desec_domain" {
    defaults = {
      minimum_ttl = 900
      keys = [{
        dnskey  = "257 3 13 WegpvoQyBO3Sfn1qJpjSDh+zrVm6LG9K9taCb5fKRhug6prfr8faHC2UFJyLj/Wcl4p5z9Mi7RjGaTA6IAFcvA=="
        ds      = ["44853 13 4 c8a8", "44853 13 2 5b11"]
        flags   = 257
        keytype = "csk"
      }]
    }
  }
}

variables {
  config = jsondecode(file("../testdata/pubkit.json")).hosting
  records = {
    "example.eu" = { apex_a = { name = "@", type = "A", ttl = 300, values = ["192.0.2.1"] } }
    "example.net" = {
      apex_a = { name = "@", type = "A", ttl = 300, values = ["192.0.2.2"] }
      mx     = { name = "@", type = "MX", ttl = 3600, values = ["10 mxext1.example.org."] }
      www    = { name = "www", type = "CNAME", ttl = 300, values = ["example.net."] }
    }
  }
  apex_nameservers = {
    "example.eu"  = null
    "example.org" = null
    "example.net" = {
      name   = "@"
      type   = "NS"
      ttl    = 86400
      values = ["ns1.cloudflare.example.", "ns1.desec.io.", "ns2.cloudflare.example.", "ns2.desec.org."]
    }
  }
}

run "zones" {
  command = apply

  assert {
    condition     = toset(keys(desec_domain.zone)) == toset(["example.net"])
    error_message = "Only the zone a desec provider serves is at deSEC."
  }

  assert {
    condition = toset(keys(desec_rrset.records)) == toset([
      "example.net apex_a", "example.net mx", "example.net www", "example.net nameservers",
    ])
    error_message = "One rrset per record of the zone, and its apex NS rrset."
  }

  assert {
    condition = (
      desec_rrset.records["example.net www"].subname == "www" &&
      desec_rrset.records["example.net apex_a"].subname == "" &&
      tolist(desec_rrset.records["example.net mx"].records) == tolist(["10 mxext1.example.org."]) &&
      toset(desec_rrset.records["example.net nameservers"].records) == toset(var.apex_nameservers["example.net"].values)
    )
    error_message = "deSEC takes names relative to the zone, \"\" for the apex, and values in zone-file form."
  }

  assert {
    condition = (
      desec_rrset.records["example.net apex_a"].ttl == 900 &&
      desec_rrset.records["example.net mx"].ttl == 3600 &&
      desec_rrset.records["example.net nameservers"].ttl == 86400
    )
    error_message = "A TTL below deSEC's minimum_ttl is raised to it; a longer one is kept."
  }

  assert {
    condition = (
      desec_rrset.dnskeys["example.net"].type == "DNSKEY" &&
      desec_rrset.dnskeys["example.net"].subname == "" &&
      desec_rrset.dnskeys["example.net"].ttl == 3600 &&
      toset(desec_rrset.dnskeys["example.net"].records) == toset(var.config.zones["example.net"].dnssec.keys.cloudflare)
    )
    error_message = "deSEC publishes Cloudflare's keys, both of them, and none of its own."
  }

  assert {
    condition = (
      jsonencode(output.ds) == jsonencode({ "example.net" = ["44853 13 2 5b11", "44853 13 4 c8a8"] }) &&
      jsonencode(output.keys) == jsonencode({ "example.net" = var.config.zones["example.net"].dnssec.keys.desec })
    )
    error_message = "Outputs ds and keys are deSEC's, by zone."
  }
}

# Unsigned, and alone: no keys to publish, and deSEC's own apex NS rrset.
run "alone_and_unsigned" {
  command = plan

  variables {
    config = {
      providers = { desec = jsondecode(file("../testdata/pubkit.json")).hosting.providers.desec }
      zones     = { "example.net" = { dns = ["desec"] } }
    }
    apex_nameservers = { "example.net" = null }
  }

  assert {
    condition     = length(desec_rrset.dnskeys) == 0 && !contains(keys(desec_rrset.records), "example.net nameservers")
    error_message = "No other provider: no keys to publish and no apex NS rrset."
  }
}
