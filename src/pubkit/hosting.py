"""The `hosting` block of pubkit.json: the hosting providers a repository
uses and what each of its zones has at them, which terraform/hosting reads.
Everything provider-specific is here, in the repository, not in pubkit:

    "hosting": {
      "ttl": {"email": 1800},
      "providers": {
        "hetzner": {"type": "hetzner",
                    "dns": {"external_nameservers": ["helium.ns.hetzner.de.", ...]}},
        "cloudflare": {"type": "cloudflare", "account_id": "0123...cdef"},
        "gcp": {"type": "google-cloud", "project": "example", "region": "europe-north1",
                "cloud_run": {"domain_target": "ghs.googlehosted.com."}},
        "mailbox": {"type": "mailbox-org", "mx": ["10 mxext1.mailbox.org.", ...],
                    "spf": "include:mailbox.org",
                    "dkim": {"mbo0001": "mbo0001._domainkey.mailbox.org.", ...}}
      },
      "zones": {
        "example.eu": {
          "dns": ["hetzner", "cloudflare"],
          "email": {"provider": "mailbox", "verification": {"f67e...": "fa0e..."},
                    "dmarc": {"policy": "none", "rua": "alerts@example.eu"}},
          "apex_txt": ["google-site-verification=..."],
          "sites": {
            "@": {"provider": "hetzner", "kind": "server", "server": "web"},
            "recipes": {"provider": "cloudflare", "kind": "pages", "project": "example-recipes",
                        "served_by": "pages", "standby": "web"},
            "pub": {"provider": "gcp", "kind": "cloud-run", "service": "pub-site"}
          }
        }
      }
    }

A provider is any name the repository chooses, with one of the types in
TYPES. `ttl`, in the hosting block, a zone or a site, sets TTLs over the
defaults (TTL_KINDS: a day for the apex NS rrset, an hour for email, five
minutes for sites); the nearest wins. A type is what pubkit supports: its settings, whether it serves
DNS, mail, and which kind of site it hosts. A new type needs pubkit to
learn it, here and in terraform/hosting; a new provider of a known type
needs only pubkit.json.

Terraform drops a setting its types do not name without a word, so
`pubkit sync` (and `sync --check`) reads the block first and stops on
anything it does not know or cannot use.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any, NoReturn

LABEL = set("abcdefghijklmnopqrstuvwxyz0123456789-_")
HEX = set("0123456789abcdef")
POLICIES = ("none", "quarantine", "reject")
SERVED_BY = ("pages", "server")
MAX_PRIORITY = 65535
ACCOUNT_ID_LENGTH = 32
TTL_KINDS = ("nameservers", "email", "sites")
TTL_RANGE = (60, 604800)


def stop(message: str) -> NoReturn:
    sys.exit(f"pubkit: pubkit.json's hosting.{message}")


def is_name(text: Any) -> bool:
    """A relative DNS name in lowercase, like mbo0001 or a.b."""
    return isinstance(text, str) and all(label and set(label) <= LABEL for label in text.split("."))


def is_host(text: Any) -> bool:
    """A fully qualified host name, with its trailing dot."""
    return isinstance(text, str) and text.endswith(".") and is_name(text[:-1])


def is_mx(text: Any) -> bool:
    """An MX value, "priority host.", as in a zone file."""
    if not isinstance(text, str) or text.count(" ") != 1:
        return False
    priority, host = text.split(" ")
    return priority.isdigit() and int(priority) <= MAX_PRIORITY and is_host(host)


def is_email(text: Any) -> bool:
    if not isinstance(text, str) or text.count("@") != 1:
        return False
    local, domain = text.split("@")
    return bool(local) and not set(local) & set(' "') and "." in domain and is_name(domain)


def is_slug(text: Any, longest: int) -> bool:
    """A lowercase name of letters, digits and hyphens, as Cloudflare Pages
    projects, Cloud Run services and Google Cloud projects are."""
    return (isinstance(text, str) and 0 < len(text) <= longest and text[0].isalnum()
            and set(text) <= LABEL - {"_"})


def is_unquoted(text: Any) -> bool:
    return isinstance(text, str) and bool(text) and '"' not in text


def known(given: Any, where: str, settings: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(given, dict):
        stop(f"{where} must be an object")
    unknown = sorted(set(given) - set(settings))
    if unknown:
        stop(f"{where} has no setting {', '.join(unknown)}; it takes {', '.join(settings)}")
    return given


def require(given: dict[str, Any], where: str, setting: str, ok: Callable[[Any], bool],
            what: str) -> None:
    if setting in given and not ok(given[setting]):
        stop(f"{where}.{setting} must be {what}")


def is_ttl(value: Any) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)
            and TTL_RANGE[0] <= value <= TTL_RANGE[1])


def check_ttl(given: Any, where: str) -> None:
    """A TTL block: seconds for the apex NS rrset, the email records and the
    sites' records, over the defaults (a day, an hour, five minutes)."""
    for kind, value in known(given, where, TTL_KINDS).items():
        if not is_ttl(value):
            stop(f"{where}.{kind} must be a number of seconds from {TTL_RANGE[0]} to {TTL_RANGE[1]}")


# --- Provider types ---

def check_hetzner(given: dict[str, Any], where: str) -> None:
    dns = known(given.get("dns", {}), f"{where}.dns", ("external_nameservers",))
    require(dns, f"{where}.dns", "external_nameservers",
            lambda v: isinstance(v, list) and all(is_host(n) for n in v),
            'a list of host names with their trailing dots, such as "helium.ns.hetzner.de."')


def check_cloudflare(given: dict[str, Any], where: str) -> None:
    account = given.get("account_id")
    if not isinstance(account, str) or len(account) != ACCOUNT_ID_LENGTH or not set(account) <= HEX:
        stop(f"{where}.account_id must be the account's id: 32 lowercase hexadecimal digits")


def check_google_cloud(given: dict[str, Any], where: str) -> None:
    if not is_slug(given.get("project"), 30):
        stop(f"{where}.project must be the Google Cloud project's id, such as \"example-prod\"")
    if not is_slug(given.get("region"), 63):
        stop(f"{where}.region must be a Google Cloud region, such as \"europe-north1\"")
    if "cloud_run" in given:
        run = known(given["cloud_run"], f"{where}.cloud_run", ("domain_target",))
        if not is_host(run.get("domain_target")):
            stop(f"{where}.cloud_run.domain_target must be the host a custom domain's CNAME "
                 'names, with its trailing dot, such as "ghs.googlehosted.com."')


def check_mailbox_org(given: dict[str, Any], where: str) -> None:
    require(given, where, "mx", lambda v: isinstance(v, list) and all(is_mx(m) for m in v),
            'a list of MX values as "priority host.", such as "10 mxext1.mailbox.org."')
    require(given, where, "spf", lambda v: is_unquoted(v) and not v.startswith("v=spf1"),
            'the SPF mechanisms that allow the provider to send, unquoted, such as '
            '"include:mailbox.org" (the zone adds "v=spf1" and "~all")')
    require(given, where, "dkim",
            lambda v: isinstance(v, dict) and all(is_name(k) and is_host(t) for k, t in v.items()),
            "an object mapping each selector, in lowercase (Hetzner refuses capitals), to the "
            'host name its key is at, such as {"mbo0001": "mbo0001._domainkey.mailbox.org."}')


TYPES: dict[str, dict[str, Any]] = {
    "hetzner": {"settings": ("dns",), "check": check_hetzner, "dns": True, "site": "server"},
    "cloudflare": {"settings": ("account_id",), "check": check_cloudflare, "dns": True,
                   "site": "pages"},
    "google-cloud": {"settings": ("project", "region", "cloud_run"), "check": check_google_cloud,
                     "dns": False, "site": "cloud-run"},
    "mailbox-org": {"settings": ("mx", "spf", "dkim"), "check": check_mailbox_org, "dns": False,
                    "mail": True},
}


def check_provider(name: str, given: Any) -> str:
    where = f"providers.{name}"
    kind = given.get("type") if isinstance(given, dict) else None
    if kind not in TYPES:
        stop(f"{where}.type must be one of {', '.join(TYPES)}; pubkit has to learn a new type "
             "before a repository can use it")
    TYPES[kind]["check"](known(given, where, ("type", *TYPES[kind]["settings"])), where)
    return kind


# --- Zones ---

def check_email(email: Any, where: str, types: dict[str, str]) -> None:
    email = known(email, where, ("provider", "verification", "dmarc"))
    if not TYPES.get(types.get(email.get("provider"), ""), {}).get("mail"):
        stop(f"{where}.provider must name a mail provider in hosting.providers")
    require(email, where, "verification",
            lambda v: isinstance(v, dict) and all(
                is_name(k) and k != "_dmarc" and is_unquoted(t) for k, t in v.items()),
            "an object mapping each name, in lowercase, to its value, unquoted")
    if "dmarc" in email:
        dmarc = known(email["dmarc"], f"{where}.dmarc", ("policy", "rua"))
        if dmarc.get("policy", "none") not in POLICIES:
            stop(f"{where}.dmarc.policy must be one of {', '.join(POLICIES)}")
        if not is_email(dmarc.get("rua")):
            stop(f"{where}.dmarc.rua must be the address reports go to, such as alerts@example.eu")


def check_server(site: dict[str, Any], where: str, setting: str) -> None:
    if not is_slug(site.get(setting), 63):
        stop(f"{where}.{setting} must name a server, whose addresses the root module gives "
             "in servers")


def check_server_site(site: dict[str, Any], where: str, _provider: dict[str, Any]) -> None:
    check_server(site, where, "server")


def check_pages_site(site: dict[str, Any], where: str, _provider: dict[str, Any]) -> None:
    if not is_slug(site.get("project"), 58):
        stop(f"{where}.project must be the Pages project's name, such as \"example-docs\"")
    if site.get("served_by", "pages") not in SERVED_BY:
        stop(f"{where}.served_by must be one of {', '.join(SERVED_BY)}")
    if site.get("served_by") == "server" or "standby" in site:
        check_server(site, where, "standby")


def check_cloud_run_site(site: dict[str, Any], where: str, provider: dict[str, Any]) -> None:
    if not is_slug(site.get("service"), 49):
        stop(f"{where}.service must be the Cloud Run service's name")
    if "cloud_run" not in provider:
        stop(f"{where} needs cloud_run.domain_target in its provider")


SITES: dict[str, tuple[tuple[str, ...], Callable[[dict[str, Any], str, dict[str, Any]], None]]] = {
    "server": (("server",), check_server_site),
    "pages": (("project", "served_by", "standby"), check_pages_site),
    "cloud-run": (("service",), check_cloud_run_site),
}


def check_site(name: str, site: Any, where: str, providers: dict[str, Any]) -> None:
    if name != "@" and (not is_name(name) or name.startswith("_")):
        stop(f"{where} must be named by the name relative to the zone, in lowercase, or \"@\"")
    provider = providers.get(site.get("provider") if isinstance(site, dict) else None, {})
    kind = TYPES.get(provider.get("type", ""), {}).get("site")
    if kind is None:
        stop(f"{where}.provider must name a provider in hosting.providers that hosts sites")
    settings_of, check = SITES[kind]
    site = known(site, where, ("provider", "kind", *settings_of, "ttl"))
    require(site, where, "ttl", is_ttl, f"a number of seconds from {TTL_RANGE[0]} to {TTL_RANGE[1]}")
    if site.get("kind") != kind:
        stop(f"{where}.kind must be {kind}, the kind of site a {provider['type']} provider hosts")
    if kind != "server" and name == "@":
        stop(f"{where} cannot be at the apex: its name is a CNAME, which the apex cannot have")
    check(site, where, provider)


def check_zone(name: str, zone: Any, providers: dict[str, Any], types: dict[str, str]) -> None:
    where = f"zones.{name}"
    if not is_name(name) or "." not in name:
        stop(f"{where} must be named by the domain, in lowercase, such as example.eu")
    zone = known(zone, where, ("dns", "ttl", "email", "apex_txt", "sites"))
    if "ttl" in zone:
        check_ttl(zone["ttl"], f"{where}.ttl")
    dns = zone.get("dns")
    if (not isinstance(dns, list) or not dns
            or not all(TYPES.get(types.get(p, ""), {}).get("dns") for p in dns)
            or len({types[p] for p in dns}) != len(dns)):
        stop(f"{where}.dns must list the providers in hosting.providers that serve the zone, "
             "at most one of each type: " + ", ".join(t for t in TYPES if TYPES[t]["dns"]))
    if "email" in zone:
        check_email(zone["email"], f"{where}.email", types)
    require(zone, where, "apex_txt", lambda v: isinstance(v, list) and all(map(is_unquoted, v)),
            "a list of TXT values, unquoted")
    sites = zone.get("sites", {})
    if not isinstance(sites, dict):
        stop(f"{where}.sites must be an object of sites by name")
    for site_name, site in sites.items():
        check_site(site_name, site, f"{where}.sites.{site_name}", providers)


def settings(config: dict[str, Any]) -> dict[str, Any]:
    """pubkit.json's hosting block, checked; {} when there is none."""
    given = config.get("hosting", {})
    if not isinstance(given, dict):
        sys.exit("pubkit: pubkit.json's hosting must be an object of providers and zones")
    unknown = sorted(set(given) - {"ttl", "providers", "zones"})
    if unknown:
        sys.exit(f"pubkit: pubkit.json's hosting has no setting {', '.join(unknown)}; "
                 "it takes ttl, providers and zones")
    if "ttl" in given:
        check_ttl(given["ttl"], "ttl")
    providers = given.get("providers", {})
    zones = given.get("zones", {})
    if not isinstance(providers, dict) or not isinstance(zones, dict):
        sys.exit("pubkit: pubkit.json's hosting.providers and hosting.zones must be objects, by name")
    types = {name: check_provider(name, provider) for name, provider in providers.items()}
    for name, zone in zones.items():
        check_zone(name, zone, providers, types)
    return given
