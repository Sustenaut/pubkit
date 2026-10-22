variable "verification_name" {
  description = "The name of mailbox.org's domain verification TXT record. The default is the one mailbox.org gives for its family-wide key."
  type        = string
  default     = "f67e29eb00a65673481c0f9731bc90e0c6f94fd3"
}

variable "verification_value" {
  description = "The security key mailbox.org shows for the domain, unquoted. It proves the domain is ours, so members can add aliases on it. Not a secret: it is published in DNS."
  type        = string
}

variable "dmarc_rua" {
  description = "Where DMARC aggregate reports go, like alerts@example.eu (without mailto:). The mailbox must exist, or an alias or catch-all for it."
  type        = string
}

variable "dmarc_policy" {
  description = "The DMARC policy: none (monitor only, never rejects mail), quarantine or reject."
  type        = string
  default     = "none"

  validation {
    condition     = contains(["none", "quarantine", "reject"], var.dmarc_policy)
    error_message = "dmarc_policy is none, quarantine or reject."
  }
}

variable "extra_apex_txt" {
  description = "Other TXT values at the apex, unquoted, like a google-site-verification=... value. Hetzner keeps one TXT rrset per name, so they share the SPF record's."
  type        = list(string)
  default     = []
}
