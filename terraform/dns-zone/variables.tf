variable "zone" {
  description = "The zone's name, like example.eu. The Hetzner zone must already exist (created in the Hetzner Console when the domain was first pointed at Hetzner DNS)."
  type        = string
}

variable "records" {
  description = <<-EOT
    Every record in the zone, one entry per rrset: a name relative to the
    zone ("@" for the apex), a type, a TTL, and its values in zone-file form
    (trailing dots, MX "priority host.", TXT quoted). Hetzner keeps one rrset
    per name and type, so each name and type appears once. The key only
    names the entry in state; "nameservers" is taken by the apex NS rrset
    this module writes when Cloudflare serves the zone too.
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

variable "cloudflare" {
  description = "Cloudflare as a second DNS provider, serving the same records, as {account_id}; null for Hetzner alone."
  type = object({
    account_id = string
  })
  default = null
}
