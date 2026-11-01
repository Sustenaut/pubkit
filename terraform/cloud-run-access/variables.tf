variable "project" {
  description = "The Google Cloud project: project in the repository's pubkit.json, under hosting.providers."
  type        = string
}

variable "region" {
  description = "The region of the services: region in the repository's pubkit.json, under hosting.providers."
  type        = string
}

variable "services" {
  description = "Who may open each service: its name, and IAM members such as user:someone@example.eu."
  type        = map(set(string))
}
