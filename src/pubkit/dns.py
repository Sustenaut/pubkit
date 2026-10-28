"""`pubkit dns check ZONE` and `pubkit dns moves`.

`pubkit dns check ZONE`: does every nameserver of a zone in pubkit.json's
hosting block give the same answers, and, for a signed zone, does each one
validate on its own?

The nameservers are the ones the zone's providers are given in pubkit.json
(dns.external_nameservers), the zone's NS rrset as resolvers find it, and
the apex NS rrset each of those serves, so a provider joining a zone is
asked before the registrar lists it. Each is asked, without recursion, for
every rrset the hosting block describes (the apex NS rrset, the mail
provider's records, each site's) and any given with --record; their
answers must be the same, TTLs aside (deSEC raises a TTL below its minimum).

With the zone's dnssec block, each nameserver must also serve exactly the
keys it pins, every provider's, and validate alone: delv asks it, and only
it, for each rrset and for a name that does not exist, with the pinned
key-signing keys as trust anchors, which is what a resolver does once the
registrar has a DS record for each. A provider that cannot sign, or one
that changed its keys, fails here before any DS record is at the registrar.

dig and delv are BIND 9's (Debian's bind9-dnsutils). Nothing is changed
anywhere: every query is a read.

`pubkit dns moves` reads `terraform show -json` on stdin and writes the
moved blocks that take terraform/dns-zone's Cloudflare records from the
position keys of pubkit 0.12.6 and earlier ("email_mx 0") to the keys by
value that replaced them ("email_mx 10 mxext1.mailbox.org"), so a root
module moves to the new keys with no change to any record.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .hosting import SEP, dnskey, settings

TIMEOUT = 30
RDATA = 4  # the fields of a record dig prints before its value: owner, TTL, class, type
KEY_FIELDS = 3  # a DNSKEY's flags, protocol and algorithm, before its key
NAME_TYPES = {"NS", "CNAME", "MX"}
VALIDATED = ("; fully validated", "; negative response, fully validated")

# What may reach dig and delv: absolute names in lower case, whose labels
# are letters, digits, hyphens and underscores and start with neither a
# hyphen nor a plus, and types in capitals. Names and types also go after
# -q and -t, so neither is ever read as an option, and nothing else (a
# name from pubkit.json, from --record, or from a nameserver's answer) is
# passed or written into delv's trust anchors.
NAME = re.compile(r"(?:[a-z0-9_][a-z0-9_-]{0,62}\.)+")
TYPE = re.compile(r"[A-Z][A-Z0-9]{0,9}")
LONGEST = 254  # an absolute name's length, its final dot included
KEY = re.compile(r"(256|257) 3 [0-9]{1,3} [A-Za-z0-9+/]+={0,2}")


@dataclass
class Answer:
    """One nameserver's answer: its status, whether it is authoritative, and
    the rrset's values, normalised so two providers' compare."""

    status: str = ""
    authoritative: bool = False
    values: frozenset[str] = field(default_factory=frozenset)


def tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        sys.exit(f"pubkit: dns check needs {name}, from BIND 9 (such as Debian's bind9-dnsutils)")
    return found


def checked(name: str) -> str:
    """An absolute name, as NAME allows; anything else stops the check."""
    if not NAME.fullmatch(name) or len(name) > LONGEST:
        sys.exit(f"pubkit: {name!r} is not a DNS name pubkit dns check asks about")
    return name


def checked_type(rtype: str) -> str:
    if not TYPE.fullmatch(rtype):
        sys.exit(f"pubkit: {rtype!r} is not a DNS record type, such as A or MX")
    return rtype


def absolute(name: str, zone: str) -> str:
    """A name relative to the zone ("@" for the apex), made absolute."""
    return checked(f"{zone}." if name == "@" else f"{name}.{zone}.")


def normalise(rtype: str, rdata: list[str]) -> str:
    """A record's value as dig prints it, in one form: host names in lower
    case, and a DNSKEY's key in one piece (dig may split a long one)."""
    if rtype in NAME_TYPES:
        return " ".join(rdata).lower()
    if rtype == "DNSKEY" and len(rdata) > KEY_FIELDS:
        return " ".join([*rdata[:KEY_FIELDS], "".join(rdata[KEY_FIELDS:])])
    return " ".join(rdata)


def dig(server: str, fqdn: str, rtype: str) -> Answer:
    """Ask one nameserver, without recursion, for one rrset."""
    out = subprocess.run(
        [tool("dig"), "+norec", "+time=3", "+tries=2", "+noall", "+comments", "+answer",
         f"@{checked(server)}", "-q", checked(fqdn), "-t", checked_type(rtype)],
        capture_output=True, text=True, timeout=TIMEOUT, check=False).stdout
    answer = Answer()
    values = set()
    for line in out.splitlines():
        if "status:" in line:
            answer.status = line.split("status:")[1].split(",")[0].strip()
        elif line.startswith(";; flags:"):
            answer.authoritative = "aa" in line.split(":")[1].split(";")[0].split()
        elif line and not line.startswith(";"):
            fields = line.split()
            if len(fields) > RDATA and fields[0].lower() == fqdn.lower() and fields[RDATA - 1] == rtype:
                values.add(normalise(rtype, fields[RDATA:]))
    answer.values = frozenset(values)
    return answer


def resolved_nameservers(zone: str) -> set[str]:
    """The zone's NS rrset as this machine's resolver finds it."""
    out = subprocess.run([tool("dig"), "+short", "-q", checked(f"{zone}."), "-t", "NS"],
                         capture_output=True, text=True, timeout=TIMEOUT, check=False).stdout
    return {line.strip().lower() for line in out.splitlines() if NAME.fullmatch(line.strip().lower())}


def configured_nameservers(zone: dict[str, Any], providers: dict[str, Any]) -> set[str]:
    return {
        f"{name.lower().rstrip('.')}."
        for provider in zone["dns"]
        for name in providers[provider].get("dns", {}).get("external_nameservers", [])
    }


def nameservers(name: str, zone: dict[str, Any], providers: dict[str, Any]) -> list[str]:
    """Every nameserver of the zone: the configured and resolved ones, and
    those any of them lists in its apex NS rrset."""
    found = configured_nameservers(zone, providers) | resolved_nameservers(name)
    for server in sorted(found):
        found |= {ns for ns in dig(server, f"{name}.", "NS").values if NAME.fullmatch(ns)}
    return sorted(found)


def rrsets(zone: dict[str, Any], providers: dict[str, Any], extra: list[str]) -> list[tuple[str, str]]:
    """The (name, type) of every rrset the hosting block describes for the
    zone, and those --record adds."""
    wanted = [("@", "NS")]
    if "dnssec" in zone:
        wanted.append(("@", "DNSKEY"))
    email = zone.get("email")
    mail = providers[email["provider"]] if email else {}
    if mail.get("mx"):
        wanted.append(("@", "MX"))
    if mail.get("spf") or zone.get("apex_txt"):
        wanted.append(("@", "TXT"))
    if email and "dmarc" in email:
        wanted.append(("_dmarc", "TXT"))
    wanted += [(f"{selector}._domainkey", "CNAME") for selector in mail.get("dkim", {})]
    wanted += [(name, "TXT") for name in (email or {}).get("verification", {})]
    for name, site in zone.get("sites", {}).items():
        cname = site["kind"] == "cloud-run" or (
            site["kind"] == "pages" and site.get("served_by", "pages") == "pages")
        wanted += [(name, "CNAME")] if cname else [(name, "A"), (name, "AAAA")]
    for record in extra:
        name, _, rtype = record.rpartition("/")
        if not name or not rtype:
            sys.exit(f"pubkit: --record {record} must be NAME/TYPE, the name relative to the zone "
                     "(\"@\" for the apex), such as www/A")
        wanted.append((name, checked_type(rtype.upper())))
    return list(dict.fromkeys(wanted))


def compare(zone: str, servers: list[str], wanted: list[tuple[str, str]]) -> list[str]:
    """Every rrset at every nameserver: each must answer for the zone, and
    all with the same values."""
    problems = []
    refusing: set[str] = set()
    for name, rtype in wanted:
        fqdn = absolute(name, zone)
        answers = {server: dig(server, fqdn, rtype) for server in servers}
        for server, answer in answers.items():
            serving = answer.authoritative and answer.status in ("NOERROR", "NXDOMAIN")
            if server not in refusing and not serving:
                refusing.add(server)
                problems.append(f"{server} does not serve {zone} (status {answer.status or 'none'}, "
                                f"{'authoritative' if answer.authoritative else 'not authoritative'})")
        given = {server: answer.values for server, answer in answers.items() if server not in refusing}
        if len(set(given.values())) > 1:
            lines = [f"  {server}: {', '.join(sorted(values)) or '(nothing)'}"
                     for server, values in sorted(given.items())]
            problems.append(f"{fqdn} {rtype} differs between nameservers:\n" + "\n".join(lines))
    return problems


def pinned_keys(dnssec: dict[str, Any]) -> set[str]:
    return {normalise("DNSKEY", key.split(" ")) for keys in dnssec["keys"].values() for key in keys}


def check_keys(zone: str, servers: list[str], pinned: set[str]) -> list[str]:
    """Each nameserver's DNSKEY rrset is the keys pubkit.json pins, all of them."""
    problems = []
    for server in servers:
        served = dig(server, f"{zone}.", "DNSKEY").values
        for key in sorted(served - pinned):
            problems.append(f"{server} serves a key pubkit.json does not pin: {key}")
        for key in sorted(pinned - served):
            problems.append(f"{server} does not serve the pinned key {key}")
    return problems


def anchors(zone: str, pinned: set[str]) -> str:
    """delv's trust anchors: the pinned key-signing keys, which the
    registrar's DS records will name."""
    lines = []
    for key in sorted(pinned):
        flags, protocol, algorithm, _ = dnskey(key)
        if flags == SEP and KEY.fullmatch(key):
            lines.append(f'  "{checked(f"{zone}.")}" static-key {flags} {protocol} {algorithm} '
                         f'"{key.split(" ")[3]}";')
    return "trust-anchors {\n" + "\n".join(lines) + "\n};\n"


def validate(zone: str, servers: list[str], wanted: list[tuple[str, str]], pinned: set[str]) -> list[str]:
    """delv at each nameserver alone, with the pinned keys as anchors: every
    rrset, and a name that does not exist, must validate."""
    problems = []
    missing = f"pubkit-check-{secrets.token_hex(6)}"
    with tempfile.TemporaryDirectory() as tmp:
        anchor_file = Path(tmp) / "anchors.conf"
        anchor_file.write_text(anchors(zone, pinned), encoding="utf-8")
        for server in servers:
            for name, rtype in [*wanted, (missing, "A")]:
                fqdn = absolute(name, zone)
                result = subprocess.run(
                    [tool("delv"), "-a", str(anchor_file), f"+root={checked(f'{zone}.')}",
                     f"@{checked(server)}", "-q", fqdn, "-t", checked_type(rtype)],
                    capture_output=True, text=True, timeout=TIMEOUT, check=False)
                lines = (result.stdout + result.stderr).splitlines()
                if not any(line.strip() in VALIDATED for line in lines):
                    reason = next((line.lstrip("; ") for line in lines if line.startswith(";;")),
                                  "no answer")
                    what = "a name that does not exist" if name == missing else f"{fqdn} {rtype}"
                    problems.append(f"{server}: {what} does not validate against the pinned keys "
                                    f"({reason})")
    return problems


SERIAL = 2  # the serial's place in an SOA value: mname rname serial ...


def lagging(zone: str, servers: list[str]) -> list[str]:
    """Nameservers serving an older version of the zone than another with
    the same primary (one provider's): written moments ago, they have not
    caught up yet, so the check is worth running again in a minute."""
    serials: dict[str, dict[str, int]] = {}
    for server in servers:
        for value in dig(server, f"{zone}.", "SOA").values:
            fields = value.split()
            if len(fields) > SERIAL and fields[SERIAL].isdigit():
                serials.setdefault(fields[0].lower(), {})[server] = int(fields[SERIAL])
    hints = []
    for primary, by_server in sorted(serials.items()):
        newest = max(by_server.values())
        for server, serial in sorted(by_server.items()):
            if serial < newest:
                hints.append(f"{server} serves serial {serial} of the zone, older than {newest} at "
                             f"the other nameservers of {primary}: it has not caught up with the "
                             "last change yet, so run the check again in a minute")
    return hints


def check(root: Path, zone: str, extra: list[str]) -> int:
    from .cli import read_config  # noqa: PLC0415
    hosting = settings(read_config(root))
    zones = hosting.get("zones", {})
    if zone not in zones:
        sys.exit(f"pubkit: pubkit.json's hosting block has no zone {zone}; it has "
                 f"{', '.join(sorted(zones)) or 'none'}")
    providers = hosting.get("providers", {})
    config = zones[zone]
    servers = nameservers(zone, config, providers)
    wanted = rrsets(config, providers, extra)
    # The DNSKEY rrset is checked against the pinned keys, which says more.
    problems = compare(zone, servers, [rrset for rrset in wanted if rrset[1] != "DNSKEY"])
    if "dnssec" in config:
        pinned = pinned_keys(config["dnssec"])
        problems += check_keys(zone, servers, pinned)
        problems += validate(zone, servers, wanted, pinned)
    for problem in problems:
        print(f"pubkit: {problem}", file=sys.stderr)
    if problems:
        for hint in lagging(zone, servers):
            print(f"pubkit: {hint}", file=sys.stderr)
        return 1
    print(f"{zone}: {len(wanted)} rrsets agree at {len(servers)} nameservers: {', '.join(servers)}")
    if "dnssec" in config:
        print(f"{zone}: every nameserver serves the {len(pinned)} pinned keys and validates alone, "
              "a name that does not exist included")
    return 0


POSITION_KEYED = {"A", "AAAA", "CNAME"}


def hcl(text: str) -> str:
    """A string as an HCL literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("${", "$${").replace("%{", "%%{") + '"'


def resources(module: dict[str, Any]):
    yield from module.get("resources", [])
    for child in module.get("child_modules", []):
        yield from resources(child)


def moves(state: str) -> int:
    """Moved blocks for every Cloudflare record of terraform/dns-zone still
    under a position key whose type pubkit now keys by value."""
    try:
        root = json.loads(state).get("values", {}).get("root_module", {})
    except (json.JSONDecodeError, AttributeError):
        sys.exit("pubkit: dns moves reads `terraform show -json` on stdin")
    blocks = []
    for resource in resources(root):
        index, address = resource.get("index"), resource.get("address", "")
        marker = ".cloudflare_dns_record.records["
        if (resource.get("type") != "cloudflare_dns_record" or resource.get("name") != "records"
                or not isinstance(index, str) or not address.startswith("module.") or marker not in address):
            continue
        key, _, position = index.rpartition(" ")
        record = resource.get("values", {})
        if not key or not position.isdigit() or record.get("type") in POSITION_KEYED:
            continue
        priority = record.get("priority")
        value = f"{priority} {record['content']}" if record.get("type") == "MX" else record["content"]
        base = address[:address.index(marker) + len(marker) - 1]
        blocks.append(f"moved {{\n  from = {base}[{hcl(index)}]\n  to   = {base}[{hcl(f'{key} {value}')}]\n}}\n")
    if blocks:
        print("# Cloudflare records keyed by value since pubkit 0.13.0 (terraform/dns-zone);\n"
              "# written by `pubkit dns moves`. Once applied, this file can go.\n")
        print("\n".join(blocks), end="")
    else:
        print("pubkit: no Cloudflare record of terraform/dns-zone is under a position key",
              file=sys.stderr)
    return 0
