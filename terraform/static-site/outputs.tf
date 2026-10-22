output "records" {
  description = "The site's rrsets, in the dns-zone module's form, to merge into its records: a CNAME to Pages, or A (and AAAA) to the standby server. Keyed by the name (dots as underscores), with _a and _aaaa for the server."
  value       = local.records
}

output "project_name" {
  description = "The Pages project's name, for the deploy action's cloudflare-project."
  value       = cloudflare_pages_project.site.name
}

output "subdomain" {
  description = "The project's pages.dev name, like example.pages.dev."
  value       = cloudflare_pages_project.site.subdomain
}

output "url" {
  description = "The site's address."
  value       = "https://${var.name}.${var.zone}/"
}
