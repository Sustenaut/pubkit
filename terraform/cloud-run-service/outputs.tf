output "service" {
  description = "The service's name."
  value       = google_cloud_run_v2_service.site.name
}

output "url" {
  description = "The service's run.app address."
  value       = google_cloud_run_v2_service.site.uri
}

output "service_account" {
  description = "The email of the account the service runs as, to give roles to."
  value       = google_service_account.site.email
}

output "service_account_member" {
  description = "That account as an IAM member."
  value       = google_service_account.site.member
}

output "image_repository" {
  description = "Where the images go: <region>-docker.pkg.dev/<project>/<name>."
  value       = local.image_repository
}

output "deploy_account" {
  description = "The deploy account's email, for the repository's workflow; null without deploy_repository."
  value       = one(google_service_account.deploy[*].email)
}

output "workload_identity_provider" {
  description = "The pool's provider for the repository's workflow, when the provider's id is the pool's, as projects/<number>/locations/global/workloadIdentityPools/<pool>/providers/<pool>."
  value       = "projects/${var.project_number}/locations/global/workloadIdentityPools/${var.workload_identity_pool}/providers/${var.workload_identity_pool}"
}
