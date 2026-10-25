# One DNS zone, its records written once and served by Hetzner, Cloudflare
# or both, and by deSEC beside them. With more than one, the records are the
# same at each, so the domain keeps resolving while one is down, and the
# registrar lists nameservers of each (output "nameservers"). Nothing else
# should add records to the zone.
#
# Each entry of var.records is one rrset in zone-file form. Hetzner takes it
# as it is; Cloudflare takes one record per value, so the Cloudflare section
# below splits them. The hosting module calls this once per zone in
# pubkit.json, with the zone's DNS providers.
#
# deSEC's records are written by terraform/desec-zones, apart because the
# deSEC provider will not start without a token, so only a repository on
# deSEC takes it. Here deSEC is only its nameservers, which the apex NS
# rrset lists (output "apex_nameservers" is that rrset, for deSEC too).
#
# With var.dnssec, the zone is signed by every provider that serves it, each
# with its own keys (RFC 8901's model 2, multi-signer): deSEC signs every
# zone it serves, Cloudflare signs once its DNSSEC is on, and each publishes
# the other's public keys, so an answer from either validates against the
# DNSKEY rrset either serves. Hetzner cannot sign, so it cannot serve a
# signed zone.

locals {
  on_hetzner     = var.hetzner != null
  on_cloudflare  = var.cloudflare != null
  on_desec       = var.desec != null
  multi_provider = length([for on in [local.on_hetzner, local.on_cloudflare, local.on_desec] : on if on]) > 1
  signed         = var.dnssec != null

  # With two or more providers, every nameserver of each, at each, so a
  # resolver that asks any one learns of all of them. With one, the apex NS
  # rrset the provider made with the zone is left as it is.
  records = merge(
    var.records,
    local.multi_provider ? {
      nameservers = {
        name   = "@"
        type   = "NS"
        ttl    = var.nameservers_ttl
        values = local.zone_nameservers
      }
    } : {},
  )

  # A registrar may refuse the nameservers Hetzner assigns a zone next to
  # another provider's: Hetzner's own (konsoleH) does. Hetzner serves the
  # same zone from a second set of names too, which it takes as external,
  # and such a registrar lists those (dns.external_nameservers of the
  # hetzner provider in pubkit.json) and Cloudflare's. The zone itself can
  # only name the assigned ones (Hetzner refuses the others as "not
  # assigned to this zone"), so the apex NS rrset lists those and
  # Cloudflare's.
  hetzner_nameservers = local.on_hetzner ? sort([
    for ns in data.hcloud_zone.zone[0].authoritative_nameservers.assigned : "${trimsuffix(ns, ".")}."
  ]) : []
  hetzner_external_nameservers = local.on_hetzner ? [
    for ns in var.hetzner.dns.external_nameservers : "${trimsuffix(ns, ".")}."
  ] : []
  hetzner_registrar_nameservers = (
    length(local.hetzner_external_nameservers) > 0 ? local.hetzner_external_nameservers : local.hetzner_nameservers
  )
  cloudflare_nameservers = local.on_cloudflare ? [
    for ns in cloudflare_zone.zone[0].name_servers : "${trimsuffix(ns, ".")}."
  ] : []

  # deSEC serves every zone from the same names, which its API does not
  # report, so they come from pubkit.json; the zone and the registrar both
  # list them.
  desec_nameservers = local.on_desec ? sort([
    for ns in var.desec.dns.external_nameservers : "${trimsuffix(ns, ".")}."
  ]) : []

  zone_nameservers      = sort(distinct(concat(local.hetzner_nameservers, local.cloudflare_nameservers, local.desec_nameservers)))
  registrar_nameservers = sort(concat(local.hetzner_registrar_nameservers, local.cloudflare_nameservers, local.desec_nameservers))

  # Cloudflare publishes every other signer's keys next to its own (deSEC
  # does the same in terraform/desec-zones). They are pinned in pubkit.json:
  # neither provider rotates its keys on its own, so a key change is a
  # reviewed change to the config. A DNSKEY record gets an hour, as each
  # provider gives its own keys.
  dnskey_ttl = 3600
  cloudflare_other_keys = local.on_cloudflare && local.signed ? sort(flatten([
    for provider, keys in var.dnssec.keys : keys if provider != "cloudflare"
  ])) : []
}

# --- Hetzner ---

data "hcloud_zone" "zone" {
  count = local.on_hetzner ? 1 : 0

  name = var.zone
}

# Hetzner keeps one rrset per name and type, which is what each entry is.
#
# Hetzner made the apex NS rrset with the zone, so when a second provider
# joins a zone already in use, it is taken over rather than created, by an
# import block in the root module (README.md, "Hosting modules").
resource "hcloud_zone_rrset" "records" {
  for_each = local.on_hetzner ? local.records : {}

  zone = data.hcloud_zone.zone[0].name
  name = each.value.name
  type = each.value.type
  ttl  = each.value.ttl

  records = [for value in each.value.values : { value = value }]
}

# --- Cloudflare ---

resource "cloudflare_zone" "zone" {
  count = local.on_cloudflare ? 1 : 0

  account = { id = var.cloudflare.account_id }
  name    = var.zone
  type    = "full"
}

# Multi-provider DNS: Cloudflare serves the zone alongside the others
# instead of waiting to be its only nameserver, and answers with the apex
# NS records, so every provider gives the same nameserver list.
resource "cloudflare_zone_dns_settings" "zone" {
  count = local.multi_provider ? 1 : 0

  zone_id        = cloudflare_zone.zone[0].id
  multi_provider = true
}

locals {
  # One Cloudflare record per value of each rrset, keyed by the rrset and
  # the value's position. The keys can't use the values themselves: some (a
  # Pages subdomain, Cloudflare's nameservers) are known only after apply,
  # and for_each keys must be known at plan time. Cloudflare lists its own
  # nameservers itself, so its NS records are the other providers' alone.
  cloudflare_records = local.on_cloudflare ? merge([
    for key, rrset in local.records : {
      for i, value in(key == "nameservers" ? sort(concat(local.hetzner_nameservers, local.desec_nameservers)) : rrset.values) : "${key} ${i}" => {
        name = rrset.name == "@" ? var.zone : "${rrset.name}.${var.zone}"
        type = rrset.type
        ttl  = rrset.ttl
        # MX values are "priority host."; Cloudflare takes them apart and
        # writes host names without the trailing dot.
        priority = rrset.type == "MX" ? tonumber(split(" ", value)[0]) : null
        content  = trimsuffix(rrset.type == "MX" ? split(" ", value)[1] : value, ".")
      }
    }
  ]...) : {}
}

# Not proxied: Cloudflare answers with the same addresses Hetzner does, and
# traffic goes straight to the server, whichever provider a resolver asked.
resource "cloudflare_dns_record" "records" {
  for_each = local.cloudflare_records

  zone_id  = cloudflare_zone.zone[0].id
  name     = each.value.name
  type     = each.value.type
  ttl      = each.value.ttl
  priority = each.value.priority
  content  = each.value.content
  proxied  = false

  # The NS records are honoured only once multi-provider DNS is on.
  depends_on = [cloudflare_zone_dns_settings.zone]
}

# Signing: Cloudflare's own keys, and multi-signer, so it serves the other
# providers' keys and does not sign the zone as if it were alone. Denial of
# existence is left as the plan has it (NSEC on Free); RFC 8901 lets each
# provider choose.
resource "cloudflare_zone_dnssec" "zone" {
  count = local.on_cloudflare && local.signed ? 1 : 0

  zone_id             = cloudflare_zone.zone[0].id
  status              = "active"
  dnssec_multi_signer = true
}

# The other providers' keys, one record each, keyed by the key as
# pubkit.json gives it ("flags 3 algorithm public-key").
resource "cloudflare_dns_record" "dnskeys" {
  for_each = toset(local.cloudflare_other_keys)

  zone_id = cloudflare_zone.zone[0].id
  name    = var.zone
  type    = "DNSKEY"
  ttl     = local.dnskey_ttl
  data = {
    flags      = tonumber(split(" ", each.value)[0])
    protocol   = tonumber(split(" ", each.value)[1])
    algorithm  = tonumber(split(" ", each.value)[2])
    public_key = split(" ", each.value)[3]
  }

  # Cloudflare takes DNSKEY records only once multi-signer is on.
  depends_on = [cloudflare_zone_dnssec.zone]
}
