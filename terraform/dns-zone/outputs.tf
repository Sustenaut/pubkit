output "nameservers" {
  description = "The nameservers to give the registrar: Hetzner's (its external set when pubkit.json gives one, else the assigned ones) and Cloudflare's."
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
