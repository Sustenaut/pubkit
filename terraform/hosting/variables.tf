variable "config" {
  description = <<-EOT
    The hosting block of the repository's pubkit.json, as
    jsondecode(file("pubkit.json")).hosting: the providers, each with its
    type and its own facts, and the zones, each naming the providers that
    serve its DNS, its email and its sites. README.md ("Hosting modules")
    describes every setting; `pubkit sync` checks the block, since
    Terraform drops a setting it does not know without a word.
  EOT
  type = object({
    # TTLs for every zone, over the module's defaults (local.default_ttl).
    ttl = optional(object({
      nameservers = optional(number)
      email       = optional(number)
      sites       = optional(number)
    }), {})

    providers = map(object({
      type = string

      # hetzner, desec
      dns = optional(object({
        external_nameservers = optional(list(string), [])
      }), {})

      # cloudflare
      account_id = optional(string)

      # google-cloud
      project = optional(string)
      region  = optional(string)
      cloud_run = optional(object({
        domain_target = string
      }))

      # mailbox-org
      mx   = optional(list(string), [])
      spf  = optional(string)
      dkim = optional(map(string), {})
    }))

    zones = map(object({
      dns = list(string)

      # The zone's TTLs, over hosting.ttl and the defaults.
      ttl = optional(object({
        nameservers = optional(number)
        email       = optional(number)
        sites       = optional(number)
      }), {})

      # Each signing provider's public keys, by the provider's name.
      dnssec = optional(object({
        keys = map(list(string))
      }))

      email = optional(object({
        provider     = string
        verification = optional(map(string), {})
        dmarc = optional(object({
          policy = optional(string, "none")
          rua    = string
        }))
      }))
      apex_txt = optional(list(string), [])
      sites = optional(map(object({
        provider  = string
        kind      = string
        server    = optional(string)
        project   = optional(string)
        subdomain = optional(string)
        served_by = optional(string, "pages")
        standby   = optional(string)
        service   = optional(string)
        ttl       = optional(number)
      })), {})
    }))
  })

  validation {
    condition = alltrue(flatten([
      [for ttl in values(var.config.ttl) : ttl == null || try(ttl >= 60 && ttl <= 604800, false)],
      [for zone in values(var.config.zones) : [for ttl in values(zone.ttl) : ttl == null || try(ttl >= 60 && ttl <= 604800, false)]],
      [for zone in values(var.config.zones) : [for site in values(zone.sites) : site.ttl == null || try(site.ttl >= 60 && site.ttl <= 604800, false)]],
    ]))
    error_message = "A TTL is a number of seconds from 60 to 604800 (a week)."
  }

  validation {
    condition = alltrue([
      for provider in values(var.config.providers) : contains(["hetzner", "cloudflare", "desec", "google-cloud", "mailbox-org"], provider.type)
    ])
    error_message = "A provider's type is hetzner, cloudflare, desec, google-cloud or mailbox-org."
  }

  validation {
    condition = alltrue([
      for zone in values(var.config.zones) : length(zone.dns) > 0 && alltrue([
        for name in zone.dns : contains(["hetzner", "cloudflare", "desec"], try(var.config.providers[name].type, ""))
      ]) && length(distinct([for name in zone.dns : var.config.providers[name].type])) == length(zone.dns)
    ])
    error_message = "A zone's dns lists providers of type hetzner, cloudflare or desec, at most one of each."
  }

  validation {
    condition = alltrue([
      for zone in values(var.config.zones) : zone.dnssec == null || try(toset(keys(zone.dnssec.keys)) == toset(zone.dns), false)
    ])
    error_message = "A zone's dnssec.keys has the keys of each provider in its dns, and no other."
  }

  validation {
    condition = alltrue([
      for zone in values(var.config.zones) : zone.email == null || try(var.config.providers[zone.email.provider].type, "") == "mailbox-org"
    ])
    error_message = "A zone's email.provider names a provider of type mailbox-org."
  }

  validation {
    condition = alltrue(flatten([
      for zone in values(var.config.zones) : [
        for site in values(zone.sites) : try({
          hetzner        = "server"
          cloudflare     = "pages"
          "google-cloud" = "cloud-run"
        }[var.config.providers[site.provider].type], "") == site.kind
      ]
    ]))
    error_message = "A site's kind is its provider's: server for hetzner, pages for cloudflare, cloud-run for google-cloud."
  }
}

variable "servers" {
  description = "The addresses of the servers that sites of kind server (and pages sites' standby) name, by the name pubkit.json uses. They are known only once the servers exist, so they come from Terraform, not pubkit.json."
  type = map(object({
    ipv4 = string
    ipv6 = optional(string)
  }))
  default = {}

  validation {
    condition = alltrue(flatten([
      for zone in values(var.config.zones) : [
        for site in values(zone.sites) : [
          for server in compact([site.server, site.standby]) : contains(keys(var.servers), server)
        ]
      ]
    ]))
    error_message = "Every server a site names in pubkit.json needs its addresses in servers."
  }
}

variable "records" {
  description = "Other records, by zone, in the dns-zone module's form: anything pubkit.json does not describe."
  type = map(map(object({
    name   = string
    type   = string
    ttl    = number
    values = list(string)
  })))
  default = {}
}
