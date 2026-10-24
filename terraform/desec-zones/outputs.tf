output "ds" {
  description = "Each zone's DS records at deSEC, for the registrar, \"key-tag algorithm digest-type digest\" (deSEC gives one per digest type); terraform/hosting's cloudflare_ds has Cloudflare's."
  value = {
    for zone_name, domain in desec_domain.zone : zone_name => sort(flatten([for key in domain.keys : tolist(key.ds)]))
  }
}

output "keys" {
  description = "Each zone's keys at deSEC, as DNSKEY values: what pubkit.json's dnssec.keys gives for the desec provider."
  value = {
    for zone_name, domain in desec_domain.zone : zone_name => sort([for key in domain.keys : key.dnskey])
  }
}
