# A static site on Cloudflare Pages, at a name in a zone the dns-zone module
# writes. The project holds files deployed from CI (actions/deploy-static):
# no Git connection, no builds on Cloudflare. A server can hold the same
# files as a standby; served_by chooses which of the two the name points
# at, so switching is one variable and one apply.

# The project's pages.dev name is its name, or that name with a suffix if
# someone else has it, so the CNAME uses the subdomain Cloudflare reports.
resource "cloudflare_pages_project" "site" {
  account_id        = var.cloudflare_account_id
  name              = var.pages_project
  production_branch = "main"
}

resource "cloudflare_pages_domain" "site" {
  account_id   = var.cloudflare_account_id
  project_name = cloudflare_pages_project.site.name
  name         = "${var.name}.${var.zone}"
}

locals {
  key = replace(var.name, ".", "_")

  records = var.served_by == "pages" ? tomap({
    (local.key) = {
      name   = var.name
      type   = "CNAME"
      ttl    = 300
      values = ["${cloudflare_pages_project.site.subdomain}."]
    }
    }) : merge(
    { "${local.key}_a" = { name = var.name, type = "A", ttl = 300, values = [var.standby_ipv4] } },
    var.standby_ipv6 == null ? {} : {
      "${local.key}_aaaa" = { name = var.name, type = "AAAA", ttl = 300, values = [var.standby_ipv6] }
    },
  )
}
