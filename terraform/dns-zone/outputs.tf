output "nameservers" {
  description = "The nameservers to give the registrar: Hetzner's (its external set when pubkit.json gives one, else the assigned ones), Cloudflare's and deSEC's."
  value       = local.registrar_nameservers
}

output "hetzner_nameservers" {
  description = "The nameservers Hetzner assigned the zone, with trailing dots; empty when Hetzner does not serve it."
  value       = local.hetzner_nameservers
}

output "cloudflare_zone_id" {
  description = "The Cloudflare zone's id, or null when Cloudflare does not serve it."
  value       = local.on_cloudflare ? cloudflare_zone.zone[0].id : null
}

output "apex_nameservers" {
  description = "The apex NS rrset this module writes when two or more providers serve the zone, in the dns-zone module's form, for terraform/desec-zones to write at deSEC too; null with one provider."
  value = local.multi_provider ? {
    name   = "@"
    type   = "NS"
    ttl    = var.nameservers_ttl
    values = local.zone_nameservers
  } : null
}

output "cloudflare_ds" {
  description = "With dnssec, Cloudflare's DS record for the registrar, \"key-tag algorithm digest-type digest\"; null without dnssec or Cloudflare, or until Cloudflare reports it."
  # Cloudflare gives it as a zone-file line: "example.eu. 3600 IN DS 2371 13 2 B30B...".
  value = try(join(" ", slice(split(" ", replace(cloudflare_zone_dnssec.zone[0].ds, "/\\s+/", " ")), 4, 8)), null)
}
