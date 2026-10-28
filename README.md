# pubkit

The testing and quality setup every package shares:
- a pytest-bdd plugin;
- the files each repository must hold, kept in step by a command;
- reusable CI workflows;
- the Rust crate standard, with a Claude Code agent that audits crates
  against it.

Every package tests its behaviour the same way: Gherkin feature files, run
by pytest-bdd, driving the built program as a user would. pubkit is that
setup, written once.

## The pytest plugin

Installed, it loads into every pytest session (the `pytest11` entry point):

| Provides | What it is |
|---|---|
| `binary(name, var=None)` | The program under test, from `<NAME>_BIN_DIR` (`pub` → `PUB_BIN_DIR`). Never from PATH, so the suite cannot quietly test whatever is installed; another implementation runs the suite by setting one variable. |
| `run(program, *args, stdin=, cwd=, env=)` | Runs a command in the test's scratch directory and returns a `Completed`: `code`, `stdout`, `stderr`, `lines`. |
| `ctx` | A per-scenario namespace. `ctx.cleanup(fn)` runs `fn` when the scenario ends, in reverse order, passed or failed. |
| common steps | `it succeeds`, `it fails`, `the exit code is N`, `it prints "…"`, `stderr mentions "…"`, `stdout is empty`. Each reads the `result` fixture a `when` step sets with `target_fixture="result"`. |
| markers | Every `@tag` in the feature files is registered, so `--strict-markers` works and `-m "not browser"` selects. |

A suite's own step with the same text wins over the plugin's.

## Keeping the files in step

```sh
pubkit init --kind=rust --bin=pub   # or --kind=utility, or --kind=service --project=NAME
pubkit lock                         # pin tests/requirements.txt, with hashes
pubkit sync --check                 # CI: fail if a managed file drifted
pubkit sync                         # after bumping pubkit: rewrite them
```

A repository is one of three kinds, named in `pubkit.json`:

| Kind | What it is |
|---|---|
| `rust` | A released Rust package, held to the Rust crate standard (below) |
| `utility` | A released tool or library in any language, or several, without the Rust package's extra checks |
| `service` | A private repository that deploys rather than releases, and runs its secret-needing commands through `./with-secrets` (below) |

The kind `python` (before 0.10.0) and `python-utility` (0.10.0) are now
`utility`: a package's language doesn't decide its kind. `sync` says so
when `pubkit.json` still names an old one.

Files are **managed**, owned by pubkit and rewritten by `sync`, or
**seeded**, written once by `init` and the repository's own after that.

- **Managed:** `tests/requirements-pubkit.txt` (the shared pins and the pubkit wheel) and `pubkit` (below). For a Rust package, also `pubkit.mk`, `rust-toolchain.toml`, and the crate standard with its agent (below). For a service, `with-secrets`.
- **Seeded:** `tests/requirements.txt` (includes the managed one), `tests/pytest.ini`, `.github/workflows/ci.yml`, for a Rust package a `Makefile` that includes `pubkit.mk`, and for a service `with-secrets.json`.

Extensions go in the seeded files, so nothing managed is ever edited by
hand. `sync --check` also fails when `tests/requirements.lock` pins another
pubkit, and in a Rust repository it runs `pubkit conform` (below).

**`./pubkit`** runs the pubkit this repository pins, whichever (if any) is
installed: `./pubkit sync --check`, `./pubkit dns check example.eu`. It
runs `uvx` on that release's wheel, which uv fetches once and caches, in
the repository's root, from whichever directory it is called. Without uv
it says how to install it. It is managed, so `pubkit sync` moves it with
the rest when a repository moves to a new pubkit.

## Services: `./with-secrets` and `service-ci.yml`

A service runs every command that needs a secret through `./with-secrets`:

```sh
./with-secrets terraform plan
./with-secrets scripts/setup-backup.sh
```

It reads a Bitwarden Secrets Manager project with the `bws` CLI, using a
read-only machine account's token (`BWS_ACCESS_TOKEN`, or that line in the
repository's git-ignored `.env`), and runs the command with the secrets in
its environment only: the calling shell never holds them, and no master
password is asked for. It stops, naming the projects the token can see, when
it cannot see the one configured. `with-secrets.json` is the repository's:

| Setting | Meaning |
|---|---|
| `project` | The project's name or id |
| `terraform.dir` | Where the Terraform files are |
| `terraform.secrets` | `declared`: a secret named like a declared variable in upper case (`HCLOUD_TOKEN` for `hcloud_token`) goes as `TF_VAR_hcloud_token` and under no other name. `map`: the names `terraform.names_from` lists go as `TF_VAR_secret_values` (declare it ephemeral) and `TF_VAR_secret_revisions` |
| `aliases` | Optional: files (paths from the repository root), each `{"aliases": {"NAME": "secret"}}`, that also export the secret named `secret` as `NAME`, for a tool that reads a fixed name (the Tailscale Terraform provider reads `TAILSCALE_OAUTH_CLIENT_ID`). Keep each file next to the code that needs it. An alias of a secret Bitwarden does not have is reported and skipped |

Every other secret goes under its name in upper case, `-` as `_`.

`service-ci.yml` is a service's CI: `pubkit sync --check` and shellcheck,
`terraform fmt`, `init -backend=false` and `validate` when `terraform-dir`
is given, and the repository's own tests when `test` is. No job has
secrets. Its `passed` job is the one check a ruleset would require; private
repositories on GitHub Free have no rulesets, so there it is a summary.

## Hosting modules

Terraform modules for a repository's domains: their DNS, their email and
their sites, each at the provider the repository chooses, and an action
that deploys a static site. They are taken from this repository by tag, and
they hold no provider's names, hosts or accounts: those are in the
repository's own `pubkit.json`, under `hosting`, so each repository keeps
its own setup.

### The hosting block

`providers` are the providers the repository uses, each under a name it
chooses, with a `type` and that provider's facts. `zones` are its domains,
each naming the providers that serve its DNS, its email and its sites.
This is [`terraform/testdata/pubkit.json`](terraform/testdata/pubkit.json),
which the modules are tested with (secrets never go here; tokens stay with
`./with-secrets`):

```json
"hosting": {
  "providers": {
    "hetzner": {
      "type": "hetzner",
      "dns": { "external_nameservers": ["helium.ns.hetzner.de.", "hydrogen.ns.hetzner.com.", "oxygen.ns.hetzner.com."] }
    },
    "cloudflare": { "type": "cloudflare", "account_id": "fedcba9876543210fedcba9876543210" },
    "desec": {
      "type": "desec",
      "dns": { "external_nameservers": ["ns1.desec.io.", "ns2.desec.org."] }
    },
    "gcp": {
      "type": "google-cloud",
      "project": "example-project",
      "region": "europe-north1",
      "cloud_run": { "domain_target": "ghs.googlehosted.com." }
    },
    "mailbox": {
      "type": "mailbox-org",
      "mx": ["10 mxext1.mailbox.org.", "10 mxext2.mailbox.org.", "10 mxext3.mailbox.org.", "10 mxext4.mailbox.org."],
      "spf": "include:mailbox.org",
      "dkim": { "mbo0001": "mbo0001._domainkey.mailbox.org.", "...": "..." }
    }
  },
  "zones": {
    "example.eu": {
      "dns": ["hetzner", "cloudflare"],
      "email": {
        "provider": "mailbox",
        "verification": { "f67e29eb00a65673481c0f9731bc90e0c6f94fd3": "<the key mailbox.org shows>" },
        "dmarc": { "policy": "none", "rua": "alerts@example.eu" }
      },
      "apex_txt": ["google-site-verification=abc"],
      "sites": {
        "@": { "provider": "hetzner", "kind": "server", "server": "web" },
        "recipes": { "provider": "cloudflare", "kind": "pages", "project": "example-recipes", "standby": "web" },
        "pub": { "provider": "gcp", "kind": "cloud-run", "service": "pub-site" }
      }
    },
    "example.net": {
      "dns": ["desec", "cloudflare"],
      "dnssec": {
        "keys": {
          "desec": ["257 3 13 Wegp...cvA=="],
          "cloudflare": ["257 3 13 o+F6...vBA==", "256 3 13 vowd...MAiQ=="]
        }
      },
      "sites": { "@": { "provider": "hetzner", "kind": "server", "server": "small" } }
    }
  }
}
```

The provider types pubkit supports, and what each holds:

| type | settings | serves DNS | sites |
| --- | --- | --- | --- |
| `hetzner` | `dns.external_nameservers`: the names Hetzner also serves a zone from, which a registrar such as Hetzner's own (konsoleH) lists next to another provider's; without them the registrar gets the assigned ones | yes; the zone must already exist at Hetzner | `server`: A (and AAAA) records to a server's addresses |
| `cloudflare` | `account_id` | yes; with another provider too, in multi-provider mode; signs with `dnssec` | `pages`: a Pages project (`project`; `subdomain` when Cloudflare gave it a pages.dev name other than `<project>.pages.dev`), served from Pages or, with `served_by: "server"`, from its `standby` server |
| `desec` | `dns.external_nameservers`: the names deSEC serves every zone from (`ns1.desec.io.`, `ns2.desec.org.`), which its API does not report | yes, through `terraform/desec-zones`; the zone must already exist at deSEC; always signs | no |
| `google-cloud` | `project`, `region`, `cloud_run.domain_target` (the host a custom domain's CNAME names) | no | `cloud-run`: a CNAME to `domain_target`, and a domain mapping to the Cloud Run `service` (`terraform/cloud-run-sites`) |
| `mailbox-org` | `mx` (`"priority host."`), `spf` (its mechanisms, such as `include:mailbox.org`), `dkim` (selector, in lowercase, to host) | no | no; a zone's `email.provider` |

A zone's `dns` lists the providers that serve its DNS, at most one of
each type; with more than one, every provider's nameservers are in the
apex NS rrset at each. Its `email` names a mail provider, with the domain's own
`verification` TXT records and `dmarc` (`policy` none, quarantine or
reject, and `rua`, where reports go). `apex_txt` holds other apex TXT
values, such as a Google site verification; they share the SPF record's
rrset, since Hetzner keeps one rrset per name and type. `sites` are by name
relative to the zone (`"@"` for the apex, which only a server site can
have, since the others are CNAMEs), each with its `provider` and that
provider's `kind`.

**DNSSEC.** A zone's `dnssec.keys` pins each of its DNS providers' public
keys, as DNSKEY values (`"flags 3 algorithm key"`, the key in base64
without spaces), by provider name. Every provider signs the zone with its
own keys and publishes the others' next to them (RFC 8901's multi-signer
model 2), so an answer from any of them validates against either DS
record at the registrar. Cloudflare's signing is turned on, with
multi-signer; deSEC signs every zone it serves. Each provider needs a
key-signing key (flags 257), and all keys one algorithm. Hetzner cannot
sign, so a zone it serves cannot have `dnssec`. The keys are pinned rather
than read from the providers: neither rotates them on its own, so a key
change is a reviewed change to `pubkit.json`. Cloudflare's are on its DNS
settings page, or `GET /zones/<id>/dnssec` (the KSK) and its DNSKEY rrset;
deSEC's are in `terraform/desec-zones`' output `keys`. Without `dnssec`,
Cloudflare does not sign.

**TTLs.** Records get a day for the apex NS rrset (written when two or
more providers serve a zone), an hour for the email records and the apex TXT
rrset, and five minutes for the sites, unless `ttl` says otherwise:
`hosting.ttl` for every zone, a zone's `ttl` for its records, or a site's
`ttl` (a number) for that site's; the nearest wins. Each is in seconds,
from 60 to a week. Lower them a TTL or more ahead of a change, so
resolvers pick it up sooner, and raise them again after.

```json
"hosting": {
  "ttl": { "email": 1800 },
  "zones": {
    "example.eu": {
      "ttl": { "sites": 60, "nameservers": 3600 },
      "sites": { "www": { "provider": "hetzner", "kind": "server", "server": "web", "ttl": 120 } }
    }
  }
}
```

A new provider of a known type is a new entry in `providers`; a repository
on two Hetzner projects names both. A new type, such as another mail
provider, needs pubkit to learn it once, in `src/pubkit/hosting.py` and
`terraform/hosting` (or a module beside it). Terraform ignores a setting its types do not name,
so `pubkit sync` (and `sync --check`, in CI) checks the block first and
stops on anything it does not know.

### Using it

The root module reads the block, gives the servers' addresses (known only
once the servers exist), and configures the Terraform providers, each with
the one account it deploys to. A repository with Cloud Run sites adds
`cloud-run-sites`:

```hcl
module "hosting" {
  source = "github.com/schryer/pubkit//terraform/hosting?ref=vX.Y.Z"
  config = jsondecode(file("${path.root}/pubkit.json")).hosting

  servers = {
    web = { ipv4 = module.web.ipv4, ipv6 = module.web.ipv6 }
  }
}

module "cloud_run_sites" {
  source = "github.com/schryer/pubkit//terraform/cloud-run-sites?ref=vX.Y.Z"
  config = jsondecode(file("${path.root}/pubkit.json")).hosting

  # When the services are created in the same apply.
  depends_on = [google_cloud_run_v2_service.pub_site]
}
```

A repository with zones at deSEC adds `desec-zones`, which writes the
same records there, and configures the deSEC provider with its token
(with `./with-secrets` and `"terraform.secrets": "declared"`, a
`DESEC_TOKEN` secret reaches it as `TF_VAR_desec_token`):

```hcl
variable "desec_token" {
  type      = string
  sensitive = true
}

provider "desec" {
  api_token = var.desec_token
  retry_max = 30
}

module "desec_zones" {
  source           = "github.com/schryer/pubkit//terraform/desec-zones?ref=vX.Y.Z"
  config           = jsondecode(file("${path.root}/pubkit.json")).hosting
  records          = module.hosting.records
  apex_nameservers = module.hosting.apex_nameservers
}
```

deSEC allows 15 rrset writes a minute (and 100 an hour) per domain, and
Terraform writes many at once, so a zone's first apply hits the limit. deSEC
then says how long to wait, and the provider waits that long before trying
again, up to `retry_max` times (5 by default, too few for a whole zone; 30
covers one of up to about 100 rrsets).

Its outputs are `nameservers` (by zone, for the registrar),
`cloudflare_zone_ids`, `records` (every rrset, by zone),
`apex_nameservers` (the apex NS rrset of each zone two or more providers
serve), `cloudflare_ds` (a signed zone's DS record at Cloudflare, for the
registrar) and `sites` (by full name: provider, kind, address, and a Pages
site's project and pages.dev name). `records` takes other rrsets, by zone,
for anything the block does not describe.

The modules need Terraform 1.11 or newer, `hcloud` 1.69, `cloudflare` 5.28,
`Valodim/desec` 0.6 and `google` 7. `terraform/hosting` needs no Cloudflare
token or provider block when no zone or site is at Cloudflare. The Google
and deSEC providers will not start without credentials, even with nothing
to do, so the Cloud Run domain mappings and the deSEC zones are in their
own modules, which only a repository on Google Cloud or deSEC takes.

- **`terraform/hosting`:** the block above, zone by zone: the records,
  the Pages projects and their domains, and each zone's DNS.
- **`terraform/cloud-run-sites`:** the Cloud Run sites' domain mappings,
  each to its `service` in its provider's `project` and `region`; output
  `domains`, with the records Google asks for.
- **`terraform/desec-zones`:** each zone a `desec` provider serves: the
  domain, every rrset `terraform/hosting` writes (a TTL below the
  domain's `minimum_ttl` raised to it, as deSEC requires), the apex NS
  rrset when other providers serve the zone too, and with `dnssec` the
  other providers' keys; outputs `ds` (deSEC's DS records, by zone, for
  the registrar) and `keys` (its DNSKEY values, for `dnssec.keys`).
- **`terraform/dns-zone`:** one zone's rrsets in zone-file form (`name`
  relative, `"@"` for the apex; trailing dots, MX `"10 host."`, TXT
  quoted), at Hetzner, Cloudflare or both, with deSEC's nameservers when
  it serves the zone too. With more than one provider, Cloudflare runs in
  multi-provider mode: one unproxied record per value (MX split into
  priority and host), and every provider's nameservers in the apex NS
  rrset at each. Each record is keyed by its rrset and its value
  (`"nameservers ns1.desec.io"`, `"email_mx 10 mxext1.mailbox.org"`), so a
  value that comes or goes changes only its own record; A, AAAA and CNAME
  records, whose values may be known only after apply, keep their position
  (`"www 0"`). With `dnssec`, Cloudflare signs in multi-signer mode and
  serves the other providers' keys as DNSKEY records; denial of existence
  is left as Cloudflare's plan has it (NSEC on Free; RFC 8901 lets each
  provider choose). `terraform/hosting` calls it for each zone; it can be
  used on its own with `hetzner`, `cloudflare`, `desec` and `dnssec` set
  as in the block (`dnssec.keys` by provider type).
- **`actions/deploy-static`:** deploys a directory to a server over rsync
  (`deploy-key`, `deploy-target`, and `known-hosts`, the server's pinned
  host key; without it the key is scanned on every run), to Cloudflare
  Pages (`cloudflare-api-token`, `cloudflare-account-id`,
  `cloudflare-project`, with wrangler pinned), or to both. It fails rather
  than deploy nowhere or to half a target.

  ```yaml
  - uses: schryer/pubkit/actions/deploy-static@vX.Y.Z
    with:
      dir: site
      cloudflare-api-token: ${{ secrets.CLOUDFLARE_PAGES_TOKEN }}
      cloudflare-account-id: ${{ vars.CLOUDFLARE_ACCOUNT_ID }}
      cloudflare-project: example-recipes
      deploy-key: ${{ secrets.DEPLOY_KEY }}
      deploy-target: deploy@example.eu:./
      known-hosts: example.eu ssh-ed25519 AAAA...
  ```

**Moving a zone that is already in use.** Hetzner made the apex NS rrset
with the zone, so the first apply with Cloudflare joining it must take it
over rather than create it, with an import block in the root module (this
is what schryer-eu-infra needed):

```hcl
import {
  to = module.hosting.module.zone["example.eu"].hcloud_zone_rrset.records["nameservers"]
  id = "example.eu/@/NS"
}
```

deSEC makes the zone's apex NS rrset too, so a zone it serves beside
another provider takes that over the same way, with the domain itself,
which must already exist at deSEC (created on desec.io):

```hcl
import {
  to = module.desec_zones.desec_domain.zone["example.eu"]
  id = "example.eu"
}

import {
  to = module.desec_zones.desec_rrset.records["example.eu nameservers"]
  id = "example.eu/@/NS"
}
```

**From pubkit 0.12.6 or earlier**, a zone at Cloudflare has its NS, MX,
TXT and other records under position keys (`"email_mx 0"`), which pubkit
now keys by value. Position keys shifted when a provider joined, and
Cloudflare refused a record created while another, being changed, still
held its value. `pubkit dns moves` writes the moved blocks from the state,
so the first plan on the new pubkit shows only moves:

```sh
./with-secrets terraform show -json | ./pubkit dns moves > moved-cloudflare-keys.tf
```

Records a root module already manages move with `moved` blocks, or with
import blocks like the one above when they were separate resources. In a
zone, the email records are `email_mx`, `email_dmarc`,
`email_dkim_<selector>` and `email_verification_<name>`, the apex TXT
rrset is `apex_txt`, and a site's are its name (`apex` for `"@"`, dots as
underscores), with `_a` and `_aaaa` for a server's addresses.

**Checking a zone.** `pubkit dns check ZONE` asks every nameserver of a
zone in the hosting block (its providers' from `pubkit.json`, the ones
resolvers find, and the ones each lists in its apex NS rrset), without
recursion, for every rrset the block describes (`--record NAME/TYPE` adds
others, such as a root module's own) and fails when their answers differ,
TTLs aside, or a nameserver does not serve the zone. With `dnssec`, each
nameserver must also serve exactly the pinned keys, and validate alone:
`delv` asks it for each rrset and for a name that does not exist, with
the pinned key-signing keys as trust anchors, as a resolver will once the
registrar has the DS records. When answers differ and one nameserver serves an older SOA serial than its provider's others (deSEC copies a change to ns2.desec.org a moment after ns1.desec.io), it says so: run it again in a minute. Run it after each step of a move, and
before any DS record goes to the registrar; it only reads, and needs
`dig` and `delv` (BIND 9; Debian's `bind9-dnsutils`).

Each module's tests (`terraform test` in its directory) mock the providers
and need no credentials; CI runs them on Terraform 1.11, with `fmt`,
`validate`, and the action's scripts under shellcheck and stubs.

## The supply-chain report

`pubkit supply-chain CRATE...` writes each crate's `SUPPLY-CHAIN.md` beside
its manifest, from the repository's `cargo vet` files (`supply-chain/`).
The report lists every crate it builds, for any target, and marks build
scripts and procedural macros. It says how each is vouched for: an imported
audit, a trusted publisher, or an exemption with its evidence. `pubkit.mk`
runs it as `make supply-chain`, and `make supply-chain-check` fails when a
committed report is stale. Set `SUPPLY_CHAIN_CRATES` in the Makefile to the
crates that are published.

## The checks the standard requires

Every `[checked]` rule in the Rust crate standard has a stable ID and names
what checks it: `[checked: RS-DOC-04, make doc-coverage-check, library]`.
`make standard` generates `standard.json` from those tags, and it ships in
pubkit. `pubkit required` reads it, and the repository's crates, and prints
the make targets each `rust-ci.yml` job must run:

```sh
$ pubkit required
gates=fmt-check lint doc doc-coverage-check publish-check
functional=functional vectors-check supply-chain-check
msrv=msrv
fuzz=fuzz-smoke
audit=deny vet
cross_job=true
...
```

A command-line tool, with no library target, is not held to the library
rules (`doc-coverage-check`). `pubkit.mk` provides every target the
standard names. A repository sets the variables they read in its Makefile:

| Variable | Default | Read by |
|---|---|---|
| `PUBLISHED_CRATES` | `SUPPLY_CHAIN_CRATES` | `doc-coverage`, `publish-check`, `msrv` |
| `VECTORS_GENERATE` | `./tools/vectors/generate.py` | `vectors`, `vectors-check` |
| `VECTORS_DIRS` | `testdata` and `crates/*/testdata`, where present | `vectors-check` |
| `FUZZ_TARGETS` | every `fuzz/fuzz_targets/*.rs` | `fuzz-smoke` |
| `MSRV` | the first `rust-version` in `Cargo.toml` | `msrv` |

`pubkit doc-coverage [--check] [CRATE...]` writes each published library's
`TESTING.md`: every public item, the examples its docs run, and the tests
whose `// covers:` comment names it. With `--check`, it fails on a public
function with no example, a `// covers:` naming no public item, or a stale
`TESTING.md`. It moved here from publet's `tools/`, so every repository runs
the same version.

## Conformance, and declared exceptions

`pubkit conform` checks what no make target can, and `pubkit sync --check`
runs it in every Rust repository:

| Rule | Checked |
|---|---|
| RS-DOC-01 | each published library declares `pub mod _documentation` |
| RS-DOC-03 | each published library sets `missing_docs` in its lints |
| RS-SAFE-01 | each published crate sets `unsafe_code = "forbid"` |
| RS-TEST-01 | `testdata/` (or `crates/*/testdata/`) exists |
| RS-FUZZ-01 | `fuzz/fuzz_targets/*.rs` exists |
| RS-SUP-01 | `deny.toml` keeps the shared baseline, and `supply-chain/config.toml` imports the shared audit sets |
| RS-SUP-04 | `SECURITY.md` exists |
| RS-SUP-05 | the release key `release.json` names is labelled for what it releases ("pubunit release key"), not with a person's name: its label is public in every release it signs |
| RS-REL-01 | `release.json` declares `pubkit audit --check` as a security check |

It also checks that every workflow calling `rust-ci.yml` pins the pubkit
version the lock pins, so a repository's CI cannot fall behind the standard
its lock names. The published crates are those that do not set
`publish = false`, or the list `"published"` in `pubkit.json` gives.

A repository that cannot meet a rule yet declares an exception in
`pubkit.json`:

```json
{ "kind": "rust",
  "exceptions": [
    { "rule": "RS-TEST-01", "reason": "no independent source for the version arithmetic yet", "until": "2027-01-01" } ] }
```

An excepted rule's conformance checks are skipped, its make targets leave
`pubkit required`'s plan, and `pubkit conform` lists it as open. An
exception past its `until` date, or naming no rule, fails.

## A release cites a current audit

The judged rules need a reader, so a release needs a report by the
rust-crate-quality agent (RS-REL-01). For a release, the agent writes it to
`audit/<package>-<version>.md`, with a header:

```
---
package: pubunit
version: 0.1.1
standard: <pubkit audit --standard>
blocking: 0
date: 2026-10-13
auditor: rust-crate-quality
---
```

`pubkit audit --check [PACKAGE VERSION]` passes only on a report for that
release, made against the standard this pubkit ships, with no finding that
blocks a release. Without arguments it reads the release `pubrel prepare` is
cutting from `PUBREL_PACKAGE` and `PUBREL_VERSION`. A repository declares it
in `release.json`:

```json
{ "name": "quality audit current", "run": ".venv/bin/pubkit audit --check",
  "version": ".venv/bin/pubkit --version" }
```

`pubrel prepare` runs every declared check before it changes anything, so it
cuts no release without a current audit. It records each check's last line
in the release's signed security record, so the release cites its report.
Changing the standard makes earlier reports stale: a release after that
needs a new audit.

## The Rust crate standard, and an agent that audits against it

[**The Rust crate standard**](plugins/rust-crate-quality/rust-crate-standard.md)
is what a crate published from these repositories is held to, whether a
library or a command-line tool. It covers:
- crate and item docs in the style of BurntSushi's crates;
- test vectors and `// covers:` links;
- fuzzing and packaging;
- Cargo's version convention, with API checks;
- the supply-chain record;
- cross-platform CI;
- plain writing.

Each rule is marked *checked* (a tool enforces it) or *judged* (it needs
reading). The standard ends with what was deliberately not adopted.

The **`rust-crate-quality` agent** audits a crate against that standard, and
writes or fixes its docs to meet it. pubkit's repository is a Claude Code
plugin marketplace, so the agent installs once for every repository, and
none of them needs a `.claude/` folder. In Claude Code:

```
/plugin marketplace add schryer/pubkit
/plugin install rust-crate-quality@pubkit
```

Then ask for it by name, in any repository:

- "Use rust-crate-quality to audit publet-core." It returns a report and
  edits nothing.
- "Use rust-crate-quality to fix the docs of pubrel." It edits
  documentation, then re-runs the checks.

Claude Code also offers it unprompted when you ask whether a crate is ready
to publish.

What it does:

1. Reads the standard. It ships with the agent, so the agent never works
   from memory.
2. Runs the checks the repository has (`make doc`, `lint`, `msrv`,
   `publish-check`, `doc-coverage-check` and the like) and reports what they
   say. It doesn't judge those rules again.
3. Builds the docs as docs.rs will: from the packaged crate, on nightly,
   with no warnings.
4. Reads the docs and code for the judged rules. It verifies claims, such
   as each "Related crates" row, rather than trusting them.
5. Reports each finding with its rule, file and line, from what blocks a
   release down to what is worth considering.

**What it changes:** documentation only. That means doc comments, the
README and `SECURITY.md`. Anything that needs a code change, such as
`#[non_exhaustive]` or a `u64` for a peer-supplied size, comes back as a
finding for you to decide.

**What it never does:** edit generated files, which it regenerates instead,
or record the change. Commit its edits as usual, with `pubrel add internal
"..."`.

`/plugin marketplace update pubkit` picks up a newer standard.

## Starting a package

    pubkit new --name=pubfoo --tag=PKG-PUBFOO-10-2026 [--description=TEXT]

In an empty repository, writes a crate that passes every check from its
first commit: the managed and seeded files, docs, test vectors, a fuzz
target, Gherkin scenarios, `deny.toml`, `supply-chain/`, `SECURITY.md`,
`release.json` with its security checks (the quality audit among them), and
a release key labelled "pubfoo release key", its secret in the gitignored
`corpus/.publet`. It refuses a `pubrel` older than 0.5.3 or a `pub` older
than 0.2.1, and prints the two steps it does not take itself: the lines
that protect its main branch and add it to `protected.json` and `governed.json`.

## Protecting main and the release tags

Every repository's default branch carries the same ruleset,
[`src/pubkit/ruleset.json`](src/pubkit/ruleset.json), which ships in the
wheel: it cannot be deleted or force-pushed, it changes only by pull
request (no approval needed), only after the required checks pass, and
nobody may bypass it. Which repositories carry it, and the checks each
requires, is local configuration, not part of pubkit:

    pubkit protect [--check] [--no-tags] [--config FILE | --require CHECK...] [REPO...]

creates the ruleset in each repository, or updates the ruleset already on
the default branch to match, renaming it. It does the same for the release
tags' ruleset, [`src/pubkit/tag-ruleset.json`](src/pubkit/tag-ruleset.json):
nobody may create, move or delete a `v*` tag, and only GitHub Actions may
bypass it. So the one way to release is the signed one: `pubrel prepare`
on your machine, then merging the release pull request, after which CI
runs `pubrel tag` on main. A tag pushed by hand is refused. A repository
that releases no tags, such as a site, goes without the tag ruleset:
`"tags": false` in its entry, or `--no-tags`. It calls the GitHub API through
`gh`, signed in as the repositories' owner, since only an admin may change
a ruleset. `--check` changes nothing: it prints how each repository
differs, a setting GitHub adds with its own default aside, and fails when
one does. When GitHub rejects a ruleset, it prints GitHub's reasons.

The repositories come from one of:

- a `protected.json` in the working directory, or the file `--config`
  names: `{"repositories": [{"repo": "OWNER/NAME", "checks": [...]}]}`, with
  `"tags": false` where the repository releases no tags.
  `REPO` arguments pick some of the repositories it lists;
- `--require CHECK`, repeated, and the `REPO` arguments, with no file:
  `pubkit protect --require "ci / passed" schryer/newrepo`.
- the repository's own `pubkit.json`, which says how it is protected:
  `"protect": {"checks": ["ci / passed"], "tags": false}` (`tags` defaults
  to true). A `REPO` the list does not name is read from the `pubkit.json`
  on its default branch, and `pubkit protect` with no list and no `REPO`,
  run inside a repository, reads `./pubkit.json` for the repository
  `origin` points at. The block takes only `checks`, distinct non-empty
  names, and `tags`, true or false; anything else, or a pubkit.json that is
  not JSON, is refused before GitHub is asked.

pubkit's own list, the pub* repositories, is
[`protected.json`](protected.json) at the root of this checkout: `ci /
passed` from a pubkit workflow, `release / release` where pubrel releases,
and `SonarCloud Code Analysis` where the SonarCloud app reports on pull
requests. Here, `make protect` (or `make protect-check`, or `make protect
REPOS=schryer/recipes`) runs it, making the venv first if there is none.

## The release board

    pubkit board --out DIR [--config protected.json] [--governed governed.json] [--rules FILE]

grades every repository the lists name, public and private, A to E for
quality, security and delivery, and writes `DIR/board.json` and a static
`DIR/index.html`: no scripts, rows worst first, each grade a dot in
SonarQube's colours whose breakdown opens on hover or keyboard focus. It
only reads, and keeps nothing between builds. `GITHUB_TOKEN` (or
`GH_TOKEN`) is the token it reads GitHub with; `SONAR_TOKEN`, when set,
reads private SonarQube Cloud projects.

The rules are [`src/pubkit/board/rules.json`](src/pubkit/board/rules.json),
applied by `rules.py`; `--rules FILE` reads others, such as a client's.
Each grade is a starting letter less one letter per notch, never below E,
with every notch listed; the overall grade is the worst of the three.

- **Quality** starts at SonarQube Cloud's worse rating of reliability and
  maintainability, **Security** at its security rating. With no analysis,
  each starts at A and takes a notch. Then each other tool with findings
  takes one: a CI job on main's last run that failed (format and lint,
  tests, functional and browser tests, MSRV, fuzzing, cross builds and
  toolchains for Quality; the dependency audit, OSV-Scanner and dependency
  review for Security), open Dependabot, code scanning or secret scanning
  alerts, and an OpenSSF Scorecard under 7 for a public repository.
  Alerts that are switched off are listed with no notch.
- **Delivery** starts at A and takes a notch for each of: a pinned pubkit
  or pubrel behind its latest release; not integrated with pubkit
  (services) or with pubkit and pubrel (packages, which have a
  release.json); for a service, no observability (its health URL does not
  answer) and no continuous live testing (its live-test workflow not green
  in the last 24 hours); no release (a package's tag, a service's
  production deploy) in two months; any open issue (pull requests aside);
  no nightly (not in governed.json, and no `nightly.yml` run in two days);
  protection that differs from what `pubkit protect --check` wants, or no
  ruleset at all on a private repository GitHub's plan gives none; and a
  red last CI run on main. A fact the board could not read takes its
  notch: a board that cannot see does not grade green.

A service declares what its observability and live testing are in its
`pubkit.json`:

```json
"live": { "health": "https://example.eu/health", "test": "live.yml", "deploy": "deploy.yml" }
```

`health` is the URL that must answer, `test` the scheduled workflow that
tests the live site, and `deploy` the workflow whose successful runs are
its production deploys (`deploy.yml` by default).

Each source has one connector in `src/pubkit/board/connectors/`, and
nothing else reads it: `lists` (the two lists), `github` (the REST API:
visibility, pubkit.json, release.json, the lock and workflow pins,
rulesets, releases, issues and alerts), `actions` (runs and jobs on main,
nightly, live-test and deploy runs), `sonarcloud` (project `OWNER_REPO`),
`scorecard` (public repositories) and `live` (the health URLs). Each
query is a named method, and each call is recorded with its connector,
method, endpoint and time: board.json lists every call, and links every
fact and every notch to the calls it rests on, which the breakdown names.
`--record FILE` keeps every response, and `--replay FILE` builds from
such a file instead of the network.

[`board.yml`](.github/workflows/board.yml) is the reusable workflow: it
checks out the caller, installs what its `tests/requirements.lock` pins
(wheels only, by hash: the caller's own pubkit),
runs `pubkit board` with the caller's read-only `token` and its lists, and
uploads board.json and index.html as the `board` artifact, kept 90 days. It
deploys nothing.

## The nightly

`.github/workflows/nightly.yml` runs every night over the repositories
`governed.json` lists. It reports, in its summary, the pubkit each pins,
whether it conforms to the latest standard, and its open exceptions
(`pubkit freshness`). Then it keeps one pull request per repository, on its
`pubkit-nightly` branch, with up to four commits (`pubkit nightly`):

1. every outdated pub* pin (`pubkit pins`): the latest pubkit in any of its
   `*-ci.yml` workflows, pubrel's release workflow and `pubrel-version`,
   `pub-version`, and the pub* crates in `Cargo.lock`;
2. the other crates' updates (`pubkit crates`) that `cargo vet` covers
   with an audit, adding no exemption, and that `cargo deny` passes. The
   body lists each update skipped with what `cargo vet suggest` says it
   needs;
3. the npm packages, each to the newest version its range in
   `package.json` allows (`npm update --package-lock-only
   --ignore-scripts`);
4. `tests/requirements.lock`, re-resolved from `requirements.txt` (`pubkit
   lock`).

Each commit regenerates `SUPPLY-CHAIN.md` where there is one and records an
`internal` pubrel row for each package it changes. An open pull request is
updated, never a second one opened, and left alone when the night brings
nothing new. When its CI fails, the pins are tested alone: if they pass,
the dependency commits are dropped. Nothing merges it: the Sustenaut bot
sends a Slack DM when it is green or fails.

### Update and security settings

A repository chooses what its nightly does in `pubkit.json`, per language
(`rust`, `node`, `python`; each applies where the repository has that
ecosystem's lock):

```json
{
  "kind": "utility",
  "update": { "rust": true, "node": true, "python": false },
  "security": {
    "fuzz": { "rust": true },
    "dependabot": { "schedule": "weekly", "ecosystems": ["github-actions"] },
    "sonar": { "args": "-Dsonar.exclusions=vendor/**" }
  }
}
```

| Setting | Default | Off means |
|---|---|---|
| `update.<language>` | `true` | the nightly pull request leaves that language's dependencies alone; `"update": false` turns every language off, leaving the pub* pins alone |
| `security.fuzz.<language>` | `true` | a scheduled run fuzzes as briefly as any other: `rust-ci.yml` runs cargo-fuzz for `fuzz-seconds` instead of `fuzz-seconds-scheduled`, and `python-ci.yml` and `node-ci.yml` give each property test `fuzz-runs` inputs instead of `fuzz-runs-scheduled` (see [Property tests](#property-tests)) |
| `security.dependabot` | weekly, `github-actions` | `pubkit sync` writes `.github/dependabot.yml` from `schedule` (`daily`, `weekly` or `monthly`), `ecosystems` (names, at the root, or Dependabot's own `{"package-ecosystem", "directory"}`) and `options` (any other Dependabot setting, on every entry). `false` leaves the file to the repository. The languages are the nightly's to update, so the default leaves them out |
| `security.sonar` | none | `args` adds SonarQube Cloud properties to every sonar job's scan, after the `sonar-args` input |

A setting pubkit does not know stops the nightly with a message, rather
than being ignored.

Advisory checks have no setting. Every run of `rust-ci.yml`, `node-ci.yml`
and `python-ci.yml`, the nightly included, checks the advisories and fails
on a finding: OSV-Scanner reads every lockfile in the repository against
the OSV database (RustSec, GitHub, PyPI and npm advisories), and each
language adds its own check: cargo deny, `npm audit` of `package-lock.json`,
or pip-audit of the hash-locked requirements. On a public repository's
pull request, GitHub's dependency review also fails a change that brings
in a vulnerable version. The pub* pins also always come,
so a governed repository keeps up with the standard. A repository's own CI
runs on a schedule when its `ci.yml` has a `schedule:` trigger; the
settings say what that run does.

### Property tests

Python and Node fuzz with property tests: a test states what holds for
every input, and the library generates inputs to break it, shrinking any
failure to a small example. Python's is [Hypothesis](https://hypothesis.readthedocs.io),
which `tests/requirements-pubkit.txt` pins for every package; Node's is
[fast-check](https://fast-check.dev), which a package adds to its own
`devDependencies`.

Each property tries `PUBKIT_FUZZ_RUNS` inputs, which `python-ci.yml` and
`node-ci.yml` set: `fuzz-runs` (100) on every push and pull request, and
`fuzz-runs-scheduled` (10000) on a scheduled run unless
`security.fuzz.<language>` is `false`. pubkit's pytest plugin reads it for
Hypothesis, with no deadline. fast-check reads no variable of its own, so a
setup file the test command loads (`node --test --import ./tests/setup.mjs`,
or Vitest's `setupFiles`) does:

```js
import fc from "fast-check";
fc.configureGlobal({ numRuns: Number(process.env.PUBKIT_FUZZ_RUNS) || 100 });
```

A failing property prints the input that broke it; Hypothesis also prints a
`@reproduce_failure` blob that replays it exactly.

## Locking

`pubkit lock` resolves with pip's own resolver (`pip install --dry-run
--report`) and needs nothing else. The lock pins the pubkit wheel by URL and
sha256: a release carries the wheel as an asset, and `pip install
--require-hashes` verifies it.

## CI

```yaml
jobs:
  ci:
    uses: schryer/pubkit/.github/workflows/rust-ci.yml@v0.1.0      # or python-ci.yml, node-ci.yml
    with: { pub-version: v0.1.1 }
    secrets: inherit                                                # for SONAR_TOKEN
  release:
    needs: ci
    uses: schryer/pubrel/.github/workflows/release.yml@v0.1.0
    with: { pub-version: v0.1.1, pubrel-version: v0.1.0 }
    permissions: { contents: write }
```

- **`rust-ci.yml`:** runs every check the standard requires. A `plan` job builds the hash-locked venv, runs `make sync-check`, and asks `pubkit required` which make targets each job runs. Then:
  - `gates` runs the required gate targets, then any the `gates` input adds;
  - `test` runs `cargo test` on Linux, macOS and Windows (`test-args` adds arguments);
  - `functional` runs the required functional targets, then any the `functional` input adds, optionally with extra apt packages and a released `pub`;
  - `msrv` runs `make msrv MSRV=<the lowest rust-version stated>` on that toolchain;
  - `fuzz` runs `make fuzz-smoke` on nightly (`fuzz-seconds`, and `fuzz-seconds-scheduled` on a schedule unless `pubkit.json` sets `"security": { "fuzz": { "rust": false } }`);
  - `cross` runs `cargo test` under qemu on `cross-targets`: i686 and s390x by default, or aarch64 `-unknown-linux-gnu`, with `cross-test-args`;
  - `audit` runs `make deny` and `make vet`, with `cargo-deny-version` and `cargo-vet-version`;
  - when the caller also runs on a `schedule`, those runs test on the latest stable and beta Rust (`scheduled-toolchains`).

  On a pull request that touches no Rust code (`*.rs`), Cargo files (`Cargo.toml`, `Cargo.lock`, the toolchain pin), `fuzz/` or workflow files (`.github/workflows/`), the plan skips `fuzz` and `cross`, the slow emulated jobs: a documentation or `pubkit.json` change does not wait for them. A release pull request (a `release/` branch), a push to main and the nightly run everything. `pubkit required --event E --head-ref B --changed-files F` decides, and says why on stderr.

  The `gates` and `functional` inputs can only add targets. The `msrv` and `fuzz` inputs are ignored: those jobs run because the standard requires them. Every job reports through `passed`, the one check a ruleset needs. The `sonar` job, in every workflow that has one and in `job-sonar.yml`, waits for SonarQube Cloud's quality gate (`sonar.qualitygate.wait`, for at most 300 seconds), so a failing gate fails the job and `passed` with it, rather than showing only on the SonarCloud app's own check. Without a `SONAR_TOKEN` it still passes with a notice.
- **`python-ci.yml`:** takes its commands as inputs (`setup`, `test`, `browser-test`), so a repository with its own entry point keeps it. A non-empty `browser-test` adds a job that installs Playwright's Chromium. The coverage it writes to `browser-coverage-report` (by default `coverage/`) reaches SonarQube Cloud with the functional job's, and the sonar job waits for it. The `audit` job checks `audit-requirements` (by default `tests/requirements.lock`) with pip-audit on every run.
- **`node-ci.yml`:** for a Node package. It installs from `package-lock.json` with `npm ci --ignore-scripts`, so no dependency runs code by being installed, checks pubkit's managed files from `tests/requirements.lock`, and runs `npm test`. The `audit` job runs `npm audit --package-lock-only` on every run and fails on any advisory, development dependencies included. A non-empty `browser-test` adds a job that installs Playwright's Chromium with `npx --no-install`, which runs only what the lock installed. The coverage it writes to `browser-coverage-report` (by default `coverage/`, LCOV) reaches SonarQube Cloud with the test job's, and the sonar job waits for it. The commands are inputs (`setup`, `test`, `browser-test`, `browser-setup`). A repository with a `ci` block in pubkit.json gets the same jobs generated instead (below).

### Generated CI: only the jobs a repository runs

A shared reusable workflow has to skip the jobs a repository doesn't use, and GitHub lists each one as skipped. A repository of any kind avoids that with a `ci` block in `pubkit.json`. `pubkit sync` then writes one workflow per event, and each workflow calls only the jobs that event runs. Each job is its own reusable workflow (`job-node-test.yml`, `job-rust-gates.yml`, `job-sonar.yml` and so on) at this pubkit's tag, so the Actions pins still live in pubkit. `pubkit init` and `pubkit new` write `"ci": {}`.

```json
"ci": {
  "events": ["pr", "main", "nightly"],
  "cron": "17 3 * * *",
  "with": {"browser-test": "npm run test:browser", "coverage-report": "coverage/",
           "sonar-args": "-Dsonar.tests=test"},
  "jobs": {"review": false, "sonar": {"events": ["pr", "main"]}},
  "release": {"pubrel": "0.5.4"}
}
```

- **`events`:** which workflows to write. `pr` writes `pr.yml` (pull requests), `main` writes `main.yml` (pushes to main), and `nightly` writes `nightly.yml` (`cron`, or by hand). The default is pr and main, and all three for Rust.
- **`with`:** the jobs' inputs, the same ones `python-ci.yml`, `node-ci.yml`, `rust-ci.yml` and `service-ci.yml` take. Each input goes to every job that takes it, and one that no job takes stops sync.
- **`jobs`:** narrows the events a job runs on, or drops it with `false` (a private repository drops `review`, since GitHub's dependency graph serves only public ones). By default every job runs on every event, except `review`, which runs on pull requests only. `browser` runs only when `with` gives a `browser-test`.
- **`language`:** a utility's, `node` or `python`. Without it, a `package.json` with no `pyproject.toml` beside it means Node. A rust or service repository's CI is its kind's.
- **`release`:** adds pubrel's release workflow to `pr.yml` and `main.yml`, after `passed`. It uses this pubkit's pubrel unless `pubrel` names one, and `with` adds inputs (Rust's always checks semver, `semver-checks-version: 0.51.0`). `false` leaves it out. A service deploys rather than releases, so it has none. `{"uses": "local"}` calls the repository's own `.github/workflows/release.yml` in place of pubrel's published one, as pubrel does, so a change to its release workflow is tested before it ships; only `with` goes with it. The nightly moves `release.pubrel` in pubkit.json, then runs `pubkit sync`.

A Rust repository runs the jobs the Rust crate standard asks for here, worked out at sync time from the rules and `pubkit.json`'s exceptions: `gates`, `test`, `functional`, `msrv`, `fuzz`, `cross` and `audit` as required, beside `osv`, `review` and `sonar`, and `toolchains` (the latest stable and beta Rust) on the nightly. Sync writes each job's required make targets into the workflow (`required`), so an exception added or expired shows as a change there, and `make sync-check` in `gates` fails until `pubkit sync` writes it. An empty `cross-targets` (`"[]"`) drops `cross`. On a pull request that touches no Rust code, Cargo files, `fuzz/` or workflow files, `fuzz` and `cross` pass at once with a notice (from `pubkit required`, which says `slow=false`) rather than being skipped; release pull requests, pushes and the nightly run them in full.

A service runs `checks` (the sync check and shellcheck), and `terraform` and `tests` when `with` gives a `terraform-dir` and a `test`.

The checks keep the names a ruleset requires: each job is named `ci`, so its checks read `ci / test`, `ci / sonar` and so on. Every workflow ends in `ci / passed`, which passes only when every job succeeded. The generated workflows are managed files, so `pubkit sync --check` fails when one is edited, and also while a hand-written `ci.yml` is still beside them. A repository without a ci block keeps its own `ci.yml`, which can still call `rust-ci.yml`, `python-ci.yml`, `node-ci.yml` or `service-ci.yml`.

### SonarQube Cloud

`rust-ci.yml`, `python-ci.yml` and `node-ci.yml` each run a `sonar` job, part of `passed`. Given a `SONAR_TOKEN` secret (pass `secrets: inherit`), it sends the code to SonarQube Cloud as the project `OWNER_REPO` in the organization `OWNER`, with the full history so it can tell new code from old; `sonar-args` adds properties. Without a token, as in a private repository or a pull request from a fork, it passes with a notice. To turn it on for a public repository:

1. In SonarQube Cloud, analyse the repository as a new project in the organization named like its GitHub owner, keeping the key `OWNER_REPO`.
2. Under the project's Administration, Analysis Method, turn Automatic Analysis off: the two cannot run together.
3. Add the token as the repository's `SONAR_TOKEN` Actions secret.

The default quality gate asks for 80% coverage on new code, and a scan without a coverage report counts it as 0%, so each workflow hands the sonar job one:

| Workflow | Report | Written by |
|---|---|---|
| `rust-ci.yml` | `coverage/lcov.info` | `make coverage`, which the functional job runs in place of `make functional` when there is a token: the Rust tests and the Gherkin suite on a build cargo-llvm-cov instruments (`cargo-llvm-cov-version`) |
| `rust-ci.yml` | `coverage/*.xml` (Cobertura) | any of the `functional` targets, for a repository's Python, such as coverage.py's `coverage xml -o coverage/scripts.xml` (run it after `make coverage`, which empties `coverage/`) |
| `python-ci.yml` | `coverage-report`, by default `coverage/coverage.xml` (Cobertura) | the `test` command, such as coverage.py with `patch = ["subprocess"]`, which also measures the programs the suite starts (pubkit's own `make functional` does this) |
| `node-ci.yml` | `coverage-report`, by default `coverage/lcov.info` | the `test` command, such as `node --test --experimental-test-coverage --test-reporter=lcov --test-reporter-destination=coverage/lcov.info` |

A test command that writes no report leaves the scan as it was.

Every action these workflows use is pinned to a commit, with its version in a comment, and a test fails on one that is not.

## Developing

```sh
make venv     # hash-locked environment, this checkout installed editable
make check    # standard.json current, then the Gherkin suite
```

The suite drives the plugin through pytest's `pytester`. Each scenario
builds a throwaway suite, runs it in a subprocess, and judges what pytest
reports. It drives the command in a scratch repository. pubkit is its own
first consumer: its CI runs through `python-ci.yml` from this checkout, and
it is released with [pubrel](https://github.com/schryer/pubrel). Its
package publet, `pkg.pubkit`, lives in `corpus/`.

## Licence

Apache-2.0: see [`LICENSE`](LICENSE).
