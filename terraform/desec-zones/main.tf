# pubkit.json's zones at deSEC: each zone a desec provider serves, with
# every record terraform/hosting writes at the other providers, so the
# answers are the same whichever a resolver asks. Apart from
# terraform/hosting because the deSEC provider will not start without a
# token, so only a repository on deSEC takes this one; the root module
# passes it terraform/hosting's records and apex_nameservers:
#
#   module "desec_zones" {
#     source           = "github.com/schryer/pubkit//terraform/desec-zones?ref=vX.Y.Z"
#     config           = jsondecode(file("${path.root}/pubkit.json")).hosting
#     records          = module.hosting.records
#     apex_nameservers = module.hosting.apex_nameservers
#   }
#
# deSEC allows 15 rrset writes a minute per domain, and a zone's first apply
# makes many: the root module's provider block sets retry_max (30), so the
# provider waits as long as deSEC says and tries again (README.md).
#
# deSEC signs every zone it serves, with one key (a CSK) it manages itself.
# With a zone's dnssec, it also serves the other providers' keys, so an
# answer from any of them validates (RFC 8901's model 2, multi-signer).

locals {
  # Each zone deSEC serves, with the name of its desec provider.
  zones = {
    for zone_name, zone in var.config.zones : zone_name => merge(zone, {
      provider = one([for name in zone.dns : name if var.config.providers[name].type == "desec"])
    }) if anytrue([for name in zone.dns : var.config.providers[name].type == "desec"])
  }

  # Every rrset of each zone, keyed "zone key". With other providers, the
  # apex NS rrset too, which lists every provider's nameservers; alone,
  # deSEC's own is left as it is.
  records = merge([
    for zone_name in keys(local.zones) : {
      for key, rrset in merge(
        lookup(var.records, zone_name, {}),
        lookup(var.apex_nameservers, zone_name, null) == null ? {} : { nameservers = var.apex_nameservers[zone_name] },
      ) : "${zone_name} ${key}" => merge(rrset, { zone = zone_name })
    }
  ]...)

  # The other signing providers' keys, as pubkit.json pins them. A DNSKEY
  # rrset gets an hour, as each provider gives its own keys.
  dnskey_ttl = 3600
  other_keys = {
    for zone_name, zone in local.zones : zone_name => sort(flatten([
      for name, keys in zone.dnssec.keys : keys if name != zone.provider
    ])) if zone.dnssec != null
  }
}

# The zone must already exist at deSEC (created on desec.io, where it starts
# signing), and is taken over with an import block, with its apex NS rrset
# where other providers serve it too: deSEC makes that rrset with the zone
# (README.md, "Hosting modules").
resource "desec_domain" "zone" {
  for_each = local.zones

  name = each.key
}

# deSEC keeps one rrset per name and type, as Hetzner does, and takes
# values in zone-file form. It refuses a TTL below the domain's minimum_ttl,
# which it sets per account, so a shorter one is raised to it: the
# providers' answers then differ only in TTL.
resource "desec_rrset" "records" {
  for_each = local.records

  domain  = desec_domain.zone[each.value.zone].name
  subname = each.value.name == "@" ? "" : each.value.name
  type    = each.value.type
  ttl     = max(each.value.ttl, desec_domain.zone[each.value.zone].minimum_ttl)
  records = each.value.values
}

# The other providers' keys. deSEC serves them next to its own, which it
# manages and its API does not show.
resource "desec_rrset" "dnskeys" {
  for_each = local.other_keys

  domain  = desec_domain.zone[each.key].name
  subname = ""
  type    = "DNSKEY"
  ttl     = max(local.dnskey_ttl, desec_domain.zone[each.key].minimum_ttl)
  records = each.value
}
