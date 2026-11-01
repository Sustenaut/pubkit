output "bucket" {
  description = "The bucket's name."
  value       = google_storage_bucket.backups.name
}

output "account" {
  description = "The email of the account that writes to it."
  value       = google_service_account.writer.email
}

output "key" {
  description = "That account's key, base64-encoded; null without create_key."
  value       = one(google_service_account_key.writer[*].private_key)
  sensitive   = true
}
