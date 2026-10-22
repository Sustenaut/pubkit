output "nameservers" {
  description = "The nameservers to give each zone's registrar, by zone."
  value       = { for zone, dns in module.zone : zone => dns.nameservers }
}

output "cloudflare_zone_ids" {
  description = "Each zone's Cloudflare zone id, or null when Cloudflare does not serve it."
  value       = { for zone, dns in module.zone : zone => dns.cloudflare_zone_id }
}

output "records" {
  description = "Every rrset this module writes, by zone, in the dns-zone module's form (the apex NS rrset of a zone two providers serve aside)."
  value = {
    for zone in keys(var.config.zones) : zone => merge(local.email_records[zone], local.site_records[zone], lookup(var.records, zone, {}))
  }
}

output "sites" {
  description = "Each site by its full name: its provider, its kind, its address, and for a Pages site the project and its pages.dev name, for the deploy action."
  value = {
    for fqdn, site in local.sites : fqdn => {
      provider  = site.provider
      kind      = site.kind
      url       = "https://${fqdn}/"
      project   = site.kind == "pages" ? cloudflare_pages_project.site[fqdn].name : null
      subdomain = site.kind == "pages" ? cloudflare_pages_project.site[fqdn].subdomain : null
    }
  }
}
