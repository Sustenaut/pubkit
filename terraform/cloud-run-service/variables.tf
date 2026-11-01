variable "name" {
  description = "The Cloud Run service's name, and its image registry's id."
  type        = string
}

variable "project" {
  description = "The Google Cloud project: project in the repository's pubkit.json, under hosting.providers."
  type        = string
}

variable "project_number" {
  description = "The project's number, in IAP's service agent and the deploy account's trust."
  type        = string
}

variable "region" {
  description = "The region: region in the repository's pubkit.json, under hosting.providers."
  type        = string
}

variable "account_id" {
  description = "Id of the account the service runs as. It gets no roles here."
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

variable "registry_description" {
  description = "The image registry's description."
  type        = string
  default     = null
}

variable "iap" {
  description = "Put Google's Identity-Aware Proxy in front of the service, and let its service agent call it. Who may pass is terraform/cloud-run-access."
  type        = bool
  default     = true
}

variable "max_instances" {
  description = "The most instances the service scales to. It scales down to none."
  type        = number
  default     = 2
}

variable "cpu" {
  description = "CPU limit of an instance."
  type        = string
  default     = "1"
}

variable "memory" {
  description = "Memory limit of an instance."
  type        = string
  default     = "256Mi"
}

variable "timeout" {
  description = "Request timeout, such as \"3600s\" for long-lived connections. Null leaves Cloud Run's default."
  type        = string
  default     = null
}

variable "session_affinity" {
  description = "Send a client's requests to the same instance."
  type        = bool
  default     = null
}

variable "concurrency" {
  description = "Requests an instance takes at once. Null leaves Cloud Run's default."
  type        = number
  default     = null
}

variable "env" {
  description = "The container's environment: each entry has a value, or a secret, the Secret Manager id whose latest version it reads. The service's account needs access to that secret."
  type = list(object({
    name   = string
    value  = optional(string)
    secret = optional(string)
  }))
  default = []

  validation {
    condition     = alltrue([for e in var.env : (e.value == null) != (e.secret == null)])
    error_message = "Each env entry has a value or a secret, not both."
  }
}

variable "deploy_repository" {
  description = "The GitHub repository (owner/name) that deploys this service. Given, this module makes an account only that repository can act as, which can push to this registry and deploy this service and nothing else. The pool's own condition must admit the repository. Null makes no deploy account."
  type        = string
  default     = null
}

variable "deploy_account_id" {
  description = "Id of the deploy account. Null is account_id with -deploy added; an id has at most 30 characters."
  type        = string
  default     = null
}

variable "workload_identity_pool" {
  description = "Id of the Workload Identity Federation pool GitHub's tokens come through, in this project."
  type        = string
  default     = "github"
}
