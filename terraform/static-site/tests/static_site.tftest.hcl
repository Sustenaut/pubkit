# Runs without credentials: Cloudflare is mocked, and gives the project a
# suffixed pages.dev name, as it does when the plain one is taken.

mock_provider "cloudflare" {
  mock_resource "cloudflare_pages_project" {
    defaults = {
      subdomain = "example-site-2ab.pages.dev"
    }
  }
}

variables {
  name                  = "recipes"
  zone                  = "example.eu"
  cloudflare_account_id = "fedcba9876543210fedcba9876543210"
  pages_project         = "example-site"
  standby_ipv4          = "192.0.2.1"
  standby_ipv6          = "2001:db8::1"
}

run "pages" {
  command = apply

  assert {
    condition     = cloudflare_pages_project.site.production_branch == "main" && cloudflare_pages_domain.site.name == "recipes.example.eu"
    error_message = "The project deploys main, at the site's full name."
  }

  assert {
    condition = jsonencode(output.records) == jsonencode({
      recipes = { name = "recipes", type = "CNAME", ttl = 300, values = ["example-site-2ab.pages.dev."] }
    })
    error_message = "One CNAME to the subdomain Cloudflare reports."
  }

  assert {
    condition     = output.subdomain == "example-site-2ab.pages.dev" && output.url == "https://recipes.example.eu/"
    error_message = "Subdomain and url outputs."
  }
}

run "server" {
  command = apply

  variables {
    served_by = "server"
  }

  assert {
    condition = jsonencode(output.records) == jsonencode({
      recipes_a    = { name = "recipes", type = "A", ttl = 300, values = ["192.0.2.1"] }
      recipes_aaaa = { name = "recipes", type = "AAAA", ttl = 300, values = ["2001:db8::1"] }
    })
    error_message = "A and AAAA to the standby server."
  }

  assert {
    condition     = cloudflare_pages_domain.site.name == "recipes.example.eu"
    error_message = "The Pages project and its domain stay while the server serves."
  }
}

run "server_ipv4_only" {
  command = plan

  variables {
    served_by    = "server"
    name         = "docs.team"
    standby_ipv6 = null
  }

  assert {
    condition     = keys(output.records) == tolist(["docs_team_a"])
    error_message = "No AAAA without an IPv6 address; dots in the name become underscores in the key."
  }
}

run "server_needs_an_address" {
  command = plan

  variables {
    served_by    = "server"
    standby_ipv4 = null
  }

  expect_failures = [var.served_by]
}

run "unknown_served_by" {
  command = plan

  variables {
    served_by = "cdn"
  }

  expect_failures = [var.served_by]
}

run "not_the_apex" {
  command = plan

  variables {
    name = "@"
  }

  expect_failures = [var.name]
}
