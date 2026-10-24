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
    this module writes when two or more providers serve the zone.
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
    condition     = var.cloudflare != null || var.hetzner != null || var.desec != null
    error_message = "Hetzner, Cloudflare, deSEC or some of them must serve the zone."
  }
}

variable "desec" {
  description = "deSEC serves the zone too, its records written by terraform/desec-zones, as a desec provider of pubkit.json's hosting block: {dns = {external_nameservers}}, the names deSEC serves every zone from, which its API does not report. Null when deSEC does not serve it."
  type = object({
    dns = object({
      external_nameservers = list(string)
    })
  })
  default = null

  validation {
    condition     = var.desec == null || try(length(var.desec.dns.external_nameservers) > 0, false)
    error_message = "deSEC's dns.external_nameservers lists the names it serves zones from, such as ns1.desec.io."
  }
}

variable "dnssec" {
  description = <<-EOT
    The zone is signed, by every provider that serves it (multi-signer):
    {keys = {cloudflare = [...], desec = [...]}}, each provider's public
    keys as DNSKEY values, "flags 3 algorithm public-key", such as
    "257 3 13 mdss...GQ==". Cloudflare signs, and publishes the others'
    keys next to its own (deSEC does so in terraform/desec-zones). Null
    leaves the zone unsigned at Cloudflare (deSEC signs every zone, but
    without a DS record at the registrar nothing validates).
  EOT
  type = object({
    keys = map(list(string))
  })
  default = null

  validation {
    condition     = var.dnssec == null || var.hetzner == null
    error_message = "Hetzner cannot sign a zone, so it cannot serve one with dnssec: a resolver would reject its unsigned answers once the registrar has a DS record."
  }

  validation {
    condition = var.dnssec == null || try(toset(keys(var.dnssec.keys)) == toset(compact([
      var.cloudflare != null ? "cloudflare" : "", var.desec != null ? "desec" : "",
    ])), false)
    error_message = "dnssec.keys has the keys of each provider that serves the zone, cloudflare and desec, and no other."
  }

  validation {
    condition = var.dnssec == null || try(alltrue(flatten([
      for keys in values(var.dnssec.keys) : [
        for key in keys : can(regex("^(256|257) 3 [0-9]+ [A-Za-z0-9+/]+={0,2}$", key))
      ]
    ])), false)
    error_message = "Each key is a DNSKEY value, \"flags 3 algorithm public-key\": flags 256 or 257, the key in base64 without spaces."
  }
}

variable "nameservers_ttl" {
  description = "The TTL of the apex NS rrset written when two or more providers serve the zone."
  type        = number
  default     = 86400
}
