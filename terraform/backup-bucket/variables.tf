variable "bucket" {
  description = "The bucket's name."
  type        = string
}

variable "project" {
  description = "The Google Cloud project: project in the repository's pubkit.json, under hosting.providers."
  type        = string
}

variable "region" {
  description = "The region the bucket is in: region in the repository's pubkit.json, under hosting.providers."
  type        = string
}

variable "retention_days" {
  description = "Objects are deleted this many days after they were made."
  type        = number
  default     = 30
}

variable "account_id" {
  description = "Id of the account that writes the backups."
  type        = string
}

variable "account_display_name" {
  description = "That account's display name."
  type        = string
}

variable "account_description" {
  description = "What that account is for."
  type        = string
  default     = null
}

variable "create_key" {
  description = "Make a key for the account, for an app outside Google Cloud. Its private half is in the state."
  type        = bool
  default     = true
}
