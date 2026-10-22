# Email through mailbox.org (send and receive): the DNS records it needs, as
# rrsets to merge into the dns-zone module's records. No resources: the
# mailboxes and aliases live in the mailbox.org account, and only the DNS
# side is here. Values from mailbox.org's custom-domain guides:
# https://kb.mailbox.org/en/private/custom-domains/using-emails-with-a-custom-domain/
# https://kb.mailbox.org/en/private/custom-domains/spf-dkim-and-dmarc/
# None are secrets.

locals {
  records = merge(
    {
      # Security key proving the domain is ours, so members can add aliases
      # on it.
      mailbox_org_verification = {
        name   = var.verification_name
        type   = "TXT"
        ttl    = 3600
        values = ["\"${var.verification_value}\""]
      }

      email_mx = {
        name = "@"
        type = "MX"
        ttl  = 3600
        values = [
          "10 mxext1.mailbox.org.",
          "10 mxext2.mailbox.org.",
          "10 mxext3.mailbox.org.",
          "10 mxext4.mailbox.org.",
        ]
      }

      # One TXT rrset per name, so any other apex TXT value (domain
      # verifications) goes here too. A domain may have only one SPF record.
      email_spf = {
        name   = "@"
        type   = "TXT"
        ttl    = 3600
        values = concat(["\"v=spf1 include:mailbox.org ~all\""], [for value in var.extra_apex_txt : "\"${value}\""])
      }

      # Start with p=none, as mailbox.org recommends, and tighten to
      # quarantine or reject once the reports look clean.
      email_dmarc = {
        name   = "_dmarc"
        type   = "TXT"
        ttl    = 3600
        values = ["\"v=DMARC1; p=${var.dmarc_policy}; rua=mailto:${var.dmarc_rua}\""]
      }
    },

    # DKIM: mailbox.org hosts and rotates the keys; we only point at them.
    # mailbox.org writes the selectors as MBO0001..; Hetzner only accepts
    # lowercase names, and DNS names are case-insensitive, so this is the
    # same.
    {
      for selector in ["mbo0001", "mbo0002", "mbo0003", "mbo0004"] :
      "email_dkim_${selector}" => {
        name   = "${selector}._domainkey"
        type   = "CNAME"
        ttl    = 3600
        values = ["${selector}._domainkey.mailbox.org."]
      }
    },
  )
}
