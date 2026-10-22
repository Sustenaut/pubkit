variable "name" {
  description = "The site's name relative to the zone, like recipes for recipes.example.eu. Not the apex: Hetzner cannot serve a CNAME there."
  type        = string

  validation {
    condition     = var.name != "@" && var.name != ""
    error_message = "name is a subdomain, not the apex."
  }
}

variable "zone" {
  description = "The zone the site is in, like example.eu."
  type        = string
}

variable "cloudflare_account_id" {
  description = "The Cloudflare account the Pages project is in."
  type        = string
}

variable "pages_project" {
  description = "The Cloudflare Pages project's name. Its pages.dev name is this, or this with a suffix if someone else has it."
  type        = string
}

variable "served_by" {
  description = "Where the name points: \"pages\" (Cloudflare Pages, normally) or \"server\" (standby_ipv4 and standby_ipv6, while Pages is out). Deploy to both, so either has the site."
  type        = string
  default     = "pages"

  validation {
    condition     = contains(["pages", "server"], var.served_by)
    error_message = "served_by is \"pages\" or \"server\"."
  }

  validation {
    condition     = var.served_by == "pages" || var.standby_ipv4 != null
    error_message = "served_by = \"server\" needs standby_ipv4."
  }
}

variable "standby_ipv4" {
  description = "The IPv4 address of the server that also has the site, which the name points at with served_by = \"server\". Null when there is none."
  type        = string
  default     = null
}

variable "standby_ipv6" {
  description = "The server's IPv6 address, or null when it has none."
  type        = string
  default     = null
}
