# Every zone in pubkit.json's hosting block: its DNS at the providers it
# names, its email records, and its sites, each at its provider. The
# providers' facts (nameservers, accounts, mail hosts) are in pubkit.json
# with everything else, so this module holds no provider's names; Terraform
# adds only what is known once things exist, a server's addresses.
#
# The root module configures the Terraform providers: hcloud, cloudflare
# and google, each with the one account it deploys to.

locals {
  providers = var.config.providers

  # Each site, with the zone it is in and its provider, keyed by its full
  # name. Within a zone, a site's records are keyed by its name, "apex" for
  # "@" and dots as underscores.
  sites = merge([
    for zone_name, zone in var.config.zones : {
      for name, site in zone.sites : (name == "@" ? zone_name : "${name}.${zone_name}") => merge(site, {
        zone     = zone_name
        name     = name
        key      = name == "@" ? "apex" : replace(name, ".", "_")
        settings = local.providers[site.provider]
      })
    }
  ]...)

  pages_sites     = { for fqdn, site in local.sites : fqdn => site if site.kind == "pages" }
  cloud_run_sites = { for fqdn, site in local.sites : fqdn => site if site.kind == "cloud-run" }

  # Where each site's name points: a CNAME, or a server's addresses.
  site_records = {
    for zone_name in keys(var.config.zones) : zone_name => merge([
      for fqdn, site in local.sites : (
        site.kind == "cloud-run" || (site.kind == "pages" && site.served_by == "pages")
        ? {
          (site.key) = {
            name   = site.name
            type   = "CNAME"
            ttl    = 300
            values = [site.kind == "pages" ? "${cloudflare_pages_project.site[fqdn].subdomain}." : site.settings.cloud_run.domain_target]
          }
        }
        : merge(
          { "${site.key}_a" = { name = site.name, type = "A", ttl = 300, values = [var.servers[coalesce(site.server, site.standby)].ipv4] } },
          var.servers[coalesce(site.server, site.standby)].ipv6 == null ? {} : {
            "${site.key}_aaaa" = { name = site.name, type = "AAAA", ttl = 300, values = [var.servers[coalesce(site.server, site.standby)].ipv6] }
          },
        )
      ) if site.zone == zone_name
    ]...)
  }

  # Each zone's email, at its mail provider, and the apex TXT rrset, which
  # holds SPF and any other apex value (Hetzner keeps one TXT rrset per
  # name, and a domain may have only one SPF record).
  email_records = {
    for zone_name, zone in var.config.zones : zone_name => merge(
      zone.email == null ? {} : merge(
        length(local.providers[zone.email.provider].mx) == 0 ? {} : {
          email_mx = { name = "@", type = "MX", ttl = 3600, values = local.providers[zone.email.provider].mx }
        },

        # Start with p=none, as mail providers recommend, and tighten to
        # quarantine or reject once the reports look clean.
        zone.email.dmarc == null ? {} : {
          email_dmarc = {
            name   = "_dmarc"
            type   = "TXT"
            ttl    = 3600
            values = ["\"v=DMARC1; p=${zone.email.dmarc.policy}; rua=mailto:${zone.email.dmarc.rua}\""]
          }
        },

        # DKIM: the provider hosts and rotates the keys; each selector only
        # points at them.
        {
          for selector, target in local.providers[zone.email.provider].dkim :
          "email_dkim_${selector}" => { name = "${selector}._domainkey", type = "CNAME", ttl = 3600, values = [target] }
        },

        # Keys proving the domain is ours to the mail provider.
        {
          for name, value in zone.email.verification :
          "email_verification_${name}" => { name = name, type = "TXT", ttl = 3600, values = ["\"${value}\""] }
        },
      ),
      length(local.apex_txt[zone_name]) == 0 ? {} : {
        apex_txt = { name = "@", type = "TXT", ttl = 3600, values = [for value in local.apex_txt[zone_name] : "\"${value}\""] }
      },
    )
  }

  apex_txt = {
    for zone_name, zone in var.config.zones : zone_name => concat(
      zone.email == null || try(local.providers[zone.email.provider].spf, null) == null ? [] : [
        "v=spf1 ${local.providers[zone.email.provider].spf} ~all"
      ],
      zone.apex_txt,
    )
  }
}

module "zone" {
  source   = "../dns-zone"
  for_each = var.config.zones

  zone    = each.key
  records = merge(local.email_records[each.key], local.site_records[each.key], lookup(var.records, each.key, {}))

  hetzner = one([
    for name in each.value.dns : { dns = local.providers[name].dns } if local.providers[name].type == "hetzner"
  ])
  cloudflare = one([
    for name in each.value.dns : { account_id = local.providers[name].account_id } if local.providers[name].type == "cloudflare"
  ])
}

# --- Cloudflare Pages sites ---
#
# Each project holds files deployed from CI (actions/deploy-static): no Git
# connection, no builds on Cloudflare. A server can hold the same files as
# a standby; served_by chooses which of the two the name points at. The
# project's pages.dev name is its name, or that name with a suffix if
# someone else has it, so the CNAME uses the subdomain Cloudflare reports.
resource "cloudflare_pages_project" "site" {
  for_each = local.pages_sites

  account_id        = each.value.settings.account_id
  name              = each.value.project
  production_branch = "main"
}

resource "cloudflare_pages_domain" "site" {
  for_each = local.pages_sites

  account_id   = each.value.settings.account_id
  project_name = cloudflare_pages_project.site[each.key].name
  name         = each.key
}

# --- Cloud Run sites ---
#
# The service itself is the repository's own (its image, scaling and
# access); this maps the site's name to it, and Google issues the
# certificate once the CNAME points at the provider's domain_target.
resource "google_cloud_run_domain_mapping" "site" {
  for_each = local.cloud_run_sites

  location = each.value.settings.region
  name     = each.key

  metadata {
    namespace = each.value.settings.project
  }
  spec {
    route_name = each.value.service
  }
}
