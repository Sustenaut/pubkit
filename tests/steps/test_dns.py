"""Steps for `pubkit dns check`, against a fake `dig` and `delv` on PATH
that answer from a JSON file: each nameserver's records, whether it serves
the zone, and the key it signs with. The fake delv validates an answer
when the nameserver's signing key is among the trust anchors it is given,
and records what it was asked."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from pubkit import dns as check

scenarios("dns.feature")

TESTDATA = Path(__file__).resolve().parents[2] / "terraform" / "testdata" / "pubkit.json"

FAKE_DIG = f"""#!{sys.executable}
import json, os, sys
state = json.load(open(os.environ["FAKE_DNS"]))
args = sys.argv[1:]
if "+short" in args:
    print("\\n".join(state["resolved"]))
    sys.exit(0)
server = next(a[1:] for a in args if a.startswith("@"))
name, rtype = args[args.index("-q") + 1], args[args.index("-t") + 1]
at = state["servers"].get(server)
if not at or not at["serves"]:
    print(";; ->>HEADER<<- opcode: QUERY, status: REFUSED, id: 1")
    print(";; flags: qr; QUERY: 1, ANSWER: 0, AUTHORITY: 0, ADDITIONAL: 1")
    sys.exit(0)
values = at["records"].get(f"{{name}} {{rtype}}", [])
print(";; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1")
print(f";; flags: qr aa; QUERY: 1, ANSWER: {{len(values)}}, AUTHORITY: 0, ADDITIONAL: 1")
print()
print(";; ANSWER SECTION:")
for value in values:
    print(f"{{name}}\\t300\\tIN\\t{{rtype}}\\t{{value}}")
"""

FAKE_DELV = f"""#!{sys.executable}
import json, os, sys
path = os.environ["FAKE_DNS"]
state = json.load(open(path))
args = sys.argv[1:]
anchors = open(args[args.index("-a") + 1]).read()
server = next(a[1:] for a in args if a.startswith("@"))
name, rtype = args[args.index("-q") + 1], args[args.index("-t") + 1]
state["delv"].append({{"server": server, "name": name, "type": rtype,
                      "root": next(a for a in args if a.startswith("+root="))}})
json.dump(state, open(path, "w"))
at = state["servers"].get(server, {{}})
if not at.get("serves") or not at.get("signed_by") or at["signed_by"] not in anchors:
    print(f";; validating {{name}}/{{rtype}}: got insecure response; parent indicates it should be secure")
    print(f";; resolution failed: insecurity proof failed")
elif any(key.startswith(name + " ") for key in at["records"]):
    print("; fully validated")
else:
    print("; negative response, fully validated")
"""


@pytest.fixture
def dns(tmp_path, env):
    """The fake DNS's state file, with `dig` and `delv` on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in (("dig", FAKE_DIG), ("delv", FAKE_DELV)):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    state = tmp_path / "dns.json"
    state.write_text(json.dumps({"resolved": [], "servers": {}, "delv": []}))
    env["FAKE_DNS"] = str(state)
    env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    return state


def _edit(dns, change):
    state = json.loads(dns.read_text())
    change(state)
    dns.write_text(json.dumps(state))


def _hosting():
    return json.loads(TESTDATA.read_text())["hosting"]


@given("pubkit.json has the hosting block the Terraform modules are tested with")
def testdata_config(repo):
    (repo / "pubkit.json").write_text(TESTDATA.read_text())


@given('every nameserver of "example.net" serves it alike')
def example_net(dns):
    """deSEC's nameservers, from pubkit.json, and Cloudflare's, which the
    resolver finds: each serves every key and signs with its provider's."""
    keys = _hosting()["zones"]["example.net"]["dnssec"]["keys"]
    desec = ["ns1.desec.io.", "ns2.desec.org."]
    cloudflare = ["ns1.cloudflare.example.", "ns2.cloudflare.example."]
    records = {
        "example.net. NS": sorted(desec + cloudflare),
        "example.net. A": ["192.0.2.2"],
        "example.net. DNSKEY": keys["desec"] + keys["cloudflare"],
    }

    def change(state):
        state["resolved"] = cloudflare
        for server in desec + cloudflare:
            signer = keys["desec" if server in desec else "cloudflare"][0]
            state["servers"][server] = {"serves": True, "signed_by": signer.split(" ")[3],
                                        "records": dict(records)}
    _edit(dns, change)


@given('every nameserver of "example.org" serves it alike')
def example_org(dns):
    """Hetzner's external nameservers, from pubkit.json, unsigned."""
    servers = _hosting()["providers"]["hetzner"]["dns"]["external_nameservers"]
    records = {
        "example.org. NS": servers,
        "auth.example.org. A": ["192.0.2.2"],
        "docs.example.org. A": ["192.0.2.2"],
    }

    def change(state):
        state["resolved"] = servers
        for server in servers:
            state["servers"][server] = {"serves": True, "signed_by": None, "records": dict(records)}
    _edit(dns, change)


@given(parsers.parse('"{server}" answers "{rrset}" with "{values}"'))
def answers(dns, server, rrset, values):
    def change(state):
        state["servers"][server]["records"][rrset] = values.split(", ")
    _edit(dns, change)


@given(parsers.parse('"{server}" does not serve the zone'))
def refuses(dns, server):
    def change(state):
        state["servers"][server]["serves"] = False
    _edit(dns, change)


@given(parsers.parse('"{server}" also serves the key "{key}"'))
def extra_key(dns, server, key):
    def change(state):
        state["servers"][server]["records"]["example.net. DNSKEY"].append(key)
    _edit(dns, change)


@given(parsers.parse('"{server}" does not serve the {provider} keys'))
def lacks_keys(dns, server, provider):
    keys = set(_hosting()["zones"]["example.net"]["dnssec"]["keys"][provider])

    def change(state):
        rrset = state["servers"][server]["records"]["example.net. DNSKEY"]
        state["servers"][server]["records"]["example.net. DNSKEY"] = [k for k in rrset if k not in keys]
    _edit(dns, change)


@given(parsers.parse('"{server}" signs with a key pubkit.json does not pin'))
def unpinned_signer(dns, server):
    def change(state):
        state["servers"][server]["signed_by"] = "c29tZXRoaW5nIGVsc2U="
    _edit(dns, change)


@given("dig is not installed")
def no_dig(dns, env):
    bin_dir = dns.parent / "bin"
    (bin_dir / "dig").unlink()
    env["PATH"] = str(bin_dir)


def _asked(dns):
    return json.loads(dns.read_text())["delv"]


@then("delv was not run")
def no_delv(dns):
    assert _asked(dns) == []


@then(parsers.parse('delv asked each of the {count:d} nameservers alone for a name that does not exist, '
                    'with "{zone}" as its root'))
def asked_missing(dns, count, zone):
    asked = [q for q in _asked(dns) if q["name"].startswith("pubkit-check-")]
    assert len({q["server"] for q in asked}) == count == len(asked), asked
    assert len({q["name"] for q in asked}) == 1, asked
    assert all(q["name"].endswith(f".{zone}.") and q["root"] == f"+root={zone}." for q in asked), asked


@then(parsers.parse('the rrsets compared for "{zone}" are "{expected}"'))
def compared(zone, expected):
    hosting = _hosting()
    wanted = check.rrsets(hosting["zones"][zone], hosting["providers"], [])
    assert [f"{name} {rtype}" for name, rtype in wanted] == expected.split(", ")


@then(parsers.parse('stderr does not mention "{text}"'))
def stderr_lacks(result, text):
    assert text not in result.stderr, result.stderr


def _record(index, rtype, content, priority=None):
    zone = 'module.hosting.module.zone["example.eu"]'
    return {"address": f'{zone}.cloudflare_dns_record.records[{json.dumps(index)}]',
            "mode": "managed", "type": "cloudflare_dns_record", "name": "records", "index": index,
            "values": {"type": rtype, "content": content, "priority": priority}}


def _state(records):
    return json.dumps({"values": {"root_module": {"child_modules": [{
        "address": "module.hosting", "child_modules": [{
            "address": 'module.hosting.module.zone["example.eu"]', "resources": records}]}]}}})


@given("a Terraform state with Cloudflare records of a zone under position keys", target_fixture="state")
def position_keyed():
    return _state([
        _record("nameservers 1", "NS", "ns1.desec.io"),
        _record("nameservers ns2.desec.org", "NS", "ns2.desec.org"),
        _record("email_mx 0", "MX", "mxext1.mailbox.org", 10),
        _record("apex_txt 0", "TXT", '"v=spf1 include:mailbox.org ~all"'),
        _record("apex_a 0", "A", "192.0.2.1"),
        _record("recipes 0", "CNAME", "example-recipes.pages.dev"),
    ])


@given("a Terraform state with no Cloudflare records under position keys", target_fixture="state")
def value_keyed():
    return _state([_record("apex_a 0", "A", "192.0.2.1")])


@given("a Terraform state that is not JSON", target_fixture="state")
def not_json():
    return "Terraform has no state"


@when("I run pubkit dns moves on it", target_fixture="result")
def run_moves(repo, run, state):
    from conftest import pubkit
    return run(pubkit(), "dns", "moves", stdin=state, cwd=repo)


@then("it moves 3 records, and none of the A, AAAA or CNAME records or those already keyed by value")
def moved_three(result):
    out = result.stdout
    assert out.count("moved {") == 3, out
    assert "apex_a" not in out and "recipes" not in out and "ns2.desec.org" not in out, out


@then("it moves the NS, MX and apex TXT records to keys by their values, HCL-quoted")
def moved_by_value(result):
    zone = 'module.hosting.module.zone["example.eu"].cloudflare_dns_record.records'
    for old, new in (("nameservers 1", "nameservers ns1.desec.io"),
                     ("email_mx 0", "email_mx 10 mxext1.mailbox.org"),
                     ("apex_txt 0", 'apex_txt \\"v=spf1 include:mailbox.org ~all\\"')):
        assert f'  from = {zone}["{old}"]\n  to   = {zone}["{new}"]\n' in result.stdout, result.stdout
