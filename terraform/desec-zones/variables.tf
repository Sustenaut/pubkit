variable "config" {
  description = "The hosting block of the repository's pubkit.json, as jsondecode(file(\"pubkit.json\")).hosting. This module reads its desec providers and the zones they serve, with their dnssec keys; terraform/hosting does the rest."
  type = object({
    providers = map(object({
      type = string
    }))
    zones = map(object({
      dns = list(string)
      dnssec = optional(object({
        keys = map(list(string))
      }))
    }))
  })
}

variable "records" {
  description = "Every rrset of each zone, by zone, in the dns-zone module's form: terraform/hosting's output records."
  type = map(map(object({
    name   = string
    type   = string
    ttl    = number
    values = list(string)
  })))
}

variable "apex_nameservers" {
  description = "Each zone's apex NS rrset where two or more providers serve it, null where one does: terraform/hosting's output apex_nameservers."
  type = map(object({
    name   = string
    type   = string
    ttl    = number
    values = list(string)
  }))
}
