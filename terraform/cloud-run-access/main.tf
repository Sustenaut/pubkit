# Who may pass Google's Identity-Aware Proxy to reach each Cloud Run service.
# The services are named, not referred to, so this can sit in a different stack
# from the services it opens: one list of people, whichever repository owns each.

locals {
  accessors = merge([
    for service, members in var.services : {
      for member in members : "${service} ${member}" => {
        service = service
        member  = member
      }
    }
  ]...)
}

resource "google_iap_web_cloud_run_service_iam_member" "accessor" {
  for_each = local.accessors

  project                = var.project
  location               = var.region
  cloud_run_service_name = each.value.service
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = each.value.member
}
