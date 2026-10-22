output "records" {
  description = "mailbox.org's rrsets, in the dns-zone module's form, to merge into its records. The SPF record and extra_apex_txt are the apex TXT rrset (key email_spf)."
  value       = local.records
}
