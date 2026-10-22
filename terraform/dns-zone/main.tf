# One DNS zone, written once and served by Hetzner, and by Cloudflare too
# when var.cloudflare is set, with the same records at both, so the domain
# keeps resolving while either one is down. The registrar lists nameservers
# of both (output "nameservers"). Nothing else should add records to the
# zone.
#
# Each entry of var.records is one rrset in zone-file form. Hetzner takes it
# as it is; Cloudflare takes one record per value, so the Cloudflare section
# below splits them.

data "hcloud_zone" "zone" {
  name = var.zone
}

locals {
  multi_provider = var.cloudflare != null

  # With both providers, every nameserver of both, at both, so a resolver
  # that asks either one learns of all of them. With Hetzner alone, the
  # apex NS rrset Hetzner made with the zone is left as it is.
  records = merge(
    var.records,
    local.multi_provider ? {
      nameservers = {
        name   = "@"
        type   = "NS"
        ttl    = 86400
        values = local.zone_nameservers
      }
    } : {},
  )

  # Hetzner's registrar (konsoleH) won't list the nameservers Hetzner
  # assigns a zone next to another provider's, but Hetzner serves the same
  # zone from a second set of names too, which it takes as external. The
  # registrar lists those and Cloudflare's. The zone itself can only name
  # the assigned ones (Hetzner refuses the others as "not assigned to this
  # zone"), so the apex NS rrset lists those and Cloudflare's.
  hetzner_registrar_nameservers = [
    "helium.ns.hetzner.de.",
    "hydrogen.ns.hetzner.com.",
    "oxygen.ns.hetzner.com.",
  ]
  hetzner_nameservers = sort([
    for ns in data.hcloud_zone.zone.authoritative_nameservers.assigned : "${trimsuffix(ns, ".")}."
  ])
  cloudflare_nameservers = local.multi_provider ? [
    for ns in cloudflare_zone.zone[0].name_servers : "${trimsuffix(ns, ".")}."
  ] : []

  zone_nameservers = sort(distinct(concat(local.hetzner_nameservers, local.cloudflare_nameservers)))
  registrar_nameservers = (
    local.multi_provider
    ? sort(concat(local.hetzner_registrar_nameservers, local.cloudflare_nameservers))
    : local.hetzner_nameservers
  )
}

# Hetzner keeps one rrset per name and type, which is what each entry is.
#
# Hetzner made the apex NS rrset with the zone, so with Cloudflare on, a
# zone already in use needs it taken over rather than created, by an import
# block in the root module (README.md, "Hosting modules").
resource "hcloud_zone_rrset" "records" {
  for_each = local.records

  zone = data.hcloud_zone.zone.name
  name = each.value.name
  type = each.value.type
  ttl  = each.value.ttl

  records = [for value in each.value.values : { value = value }]
}

# --- Cloudflare: the second provider, when var.cloudflare is set ---

resource "cloudflare_zone" "zone" {
  count = local.multi_provider ? 1 : 0

  account = { id = var.cloudflare.account_id }
  name    = var.zone
  type    = "full"
}

# Multi-provider DNS: Cloudflare serves the zone alongside Hetzner instead
# of waiting to be its only nameserver, and answers with the apex NS
# records, so both providers give the same nameserver list.
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
  # nameservers itself, so its NS records are Hetzner's alone.
  cloudflare_records = local.multi_provider ? merge([
    for key, rrset in local.records : {
      for i, value in(key == "nameservers" ? local.hetzner_nameservers : rrset.values) : "${key} ${i}" => {
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
