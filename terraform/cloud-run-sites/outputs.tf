output "domains" {
  description = "Each Cloud Run site's full name, with the service it is mapped to and the records Google asks for (empty until the mapping exists)."
  value = {
    for fqdn, mapping in google_cloud_run_domain_mapping.site : fqdn => {
      service = mapping.spec[0].route_name
      records = try(mapping.status[0].resource_records, [])
    }
  }
}
