# No resources and no providers: the module only builds rrsets.

variables {
  verification_value = "0123456789abcdef0123456789abcdef01234567"
  dmarc_rua          = "alerts@example.eu"
}

run "defaults" {
  command = plan

  assert {
    condition = toset(keys(output.records)) == toset([
      "mailbox_org_verification", "email_mx", "email_spf", "email_dmarc",
      "email_dkim_mbo0001", "email_dkim_mbo0002", "email_dkim_mbo0003", "email_dkim_mbo0004",
    ])
    error_message = "Verification, MX, SPF, DMARC and four DKIM rrsets."
  }

  assert {
    condition = output.records["email_mx"].name == "@" && tolist(output.records["email_mx"].values) == tolist([
      "10 mxext1.mailbox.org.", "10 mxext2.mailbox.org.", "10 mxext3.mailbox.org.", "10 mxext4.mailbox.org.",
    ])
    error_message = "Four mailbox.org MX hosts at the apex, priority first."
  }

  assert {
    condition     = tolist(output.records["email_spf"].values) == tolist(["\"v=spf1 include:mailbox.org ~all\""])
    error_message = "SPF alone at the apex without extra_apex_txt."
  }

  assert {
    condition = (
      output.records["email_dmarc"].name == "_dmarc" &&
      tolist(output.records["email_dmarc"].values) == tolist(["\"v=DMARC1; p=none; rua=mailto:alerts@example.eu\""])
    )
    error_message = "Monitor-only DMARC, reporting to dmarc_rua."
  }

  assert {
    condition = (
      output.records["mailbox_org_verification"].name == "f67e29eb00a65673481c0f9731bc90e0c6f94fd3" &&
      tolist(output.records["mailbox_org_verification"].values) == tolist(["\"0123456789abcdef0123456789abcdef01234567\""])
    )
    error_message = "The verification key, quoted, at mailbox.org's name."
  }

  assert {
    condition = (
      output.records["email_dkim_mbo0003"].name == "mbo0003._domainkey" &&
      output.records["email_dkim_mbo0003"].type == "CNAME" &&
      tolist(output.records["email_dkim_mbo0003"].values) == tolist(["mbo0003._domainkey.mailbox.org."])
    )
    error_message = "DKIM selectors are lowercase CNAMEs to mailbox.org."
  }
}

run "extra_txt_and_policy" {
  command = plan

  variables {
    extra_apex_txt = ["google-site-verification=abc"]
    dmarc_policy   = "quarantine"
  }

  assert {
    condition = tolist(output.records["email_spf"].values) == tolist([
      "\"v=spf1 include:mailbox.org ~all\"", "\"google-site-verification=abc\"",
    ])
    error_message = "Extra apex TXT values share the SPF rrset, quoted."
  }

  assert {
    condition     = strcontains(one(output.records["email_dmarc"].values), "p=quarantine;")
    error_message = "dmarc_policy sets p=."
  }
}

run "unknown_policy" {
  command = plan

  variables {
    dmarc_policy = "block"
  }

  expect_failures = [var.dmarc_policy]
}
