output "nameservers" {
  description = "The nameservers to give the registrar: Hetzner's external set and Cloudflare's with Cloudflare on, Hetzner's assigned ones alone without."
  value       = local.registrar_nameservers
}

output "hetzner_nameservers" {
  description = "The nameservers Hetzner assigned the zone, with trailing dots."
  value       = local.hetzner_nameservers
}

output "cloudflare_zone_id" {
  description = "The Cloudflare zone's id, or null without Cloudflare."
  value       = local.multi_provider ? cloudflare_zone.zone[0].id : null
}
