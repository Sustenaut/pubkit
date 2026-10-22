variable "zone" {
  description = "The zone's name, like example.eu."
  type        = string
}

variable "records" {
  description = <<-EOT
    Every record in the zone, one entry per rrset: a name relative to the
    zone ("@" for the apex), a type, a TTL, and its values in zone-file form
    (trailing dots, MX "priority host.", TXT quoted). Hetzner keeps one rrset
    per name and type, so each name and type appears once. The key only
    names the entry in state; "nameservers" is taken by the apex NS rrset
    this module writes when two providers serve the zone.
  EOT
  type = map(object({
    name   = string
    type   = string
    ttl    = number
    values = list(string)
  }))

  validation {
    condition     = !contains(keys(var.records), "nameservers")
    error_message = "The key \"nameservers\" is the module's own apex NS rrset; give the record another key."
  }

  validation {
    condition = length(var.records) == length(distinct([
      for rrset in var.records : "${rrset.name} ${upper(rrset.type)}"
    ]))
    error_message = "Two entries have the same name and type; Hetzner keeps one rrset per name and type, so put their values in one entry."
  }
}

variable "hetzner" {
  description = "Hetzner serves the zone, which must already exist there (created in the Hetzner Console), as a hetzner provider of pubkit.json's hosting block: {dns = {external_nameservers}}. Null when Hetzner does not serve it."
  type = object({
    dns = optional(object({
      external_nameservers = optional(list(string), [])
    }), {})
  })
  default = null
}

variable "cloudflare" {
  description = "Cloudflare serves the zone, as a cloudflare provider of pubkit.json's hosting block: {account_id}. Null when Cloudflare does not serve it."
  type = object({
    account_id = string
  })
  default = null

  validation {
    condition     = var.cloudflare != null || var.hetzner != null
    error_message = "Hetzner, Cloudflare or both must serve the zone."
  }
}

variable "nameservers_ttl" {
  description = "The TTL of the apex NS rrset written when two providers serve the zone."
  type        = number
  default     = 86400
}
