"""Steps for `pubkit protect`: the ruleset itself, the shipped
protected.json, and the command against a fake `gh` that keeps each
repository's rulesets in a JSON file and records what it was sent."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from pubkit import protect

scenarios("protect.feature")

ROOT = Path(__file__).resolve().parents[2]

FAKE_GH = f"""#!{sys.executable}
import json, os, re, sys
path = os.environ["FAKE_GH"]
state = json.load(open(path))
args = sys.argv[1:]
method, url = args[args.index("-X") + 1], args[-1] if "--input" not in args else args[args.index("--input") - 1]
m = re.match(r"repos/([^/]+/[^/]+)/rulesets(?:/(\\d+))?", url)
repo, rid = m.group(1), m.group(2)
rulesets = state["rulesets"].setdefault(repo, [])
if method != "GET":
    if repo in state["refuse"]:
        sys.stderr.write("gh: Resource not accessible by integration (HTTP 403)\\n")
        sys.exit(1)
    if repo in state["reject"]:
        print(json.dumps({{"message": "Validation Failed", "errors": state["reject"][repo]}}))
        sys.stderr.write("gh: Validation Failed (HTTP 422)\\n")
        sys.exit(1)
    state["sent"].append({{"method": method, "url": url, "body": json.load(sys.stdin)}})
    json.dump(state, open(path, "w"))
    print("{{}}")
elif rid:
    print(json.dumps(next(r for r in rulesets if str(r["id"]) == rid)))
else:
    print(json.dumps([{{k: r[k] for k in ("id", "name", "target")}} for r in rulesets]))
"""


@pytest.fixture
def github(tmp_path, env):
    """The fake GitHub's state file, with `gh` on PATH for the command."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    state = tmp_path / "github.json"
    state.write_text(json.dumps({"rulesets": {}, "refuse": [], "reject": {}, "sent": []}))
    env["FAKE_GH"] = str(state)
    env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    return state


def _edit(github, change):
    state = json.loads(github.read_text())
    change(state)
    github.write_text(json.dumps(state))


def _checks(text):
    return [c.strip() for c in text.split(",")]


@when(parsers.parse('I build the ruleset requiring "{checks}"'), target_fixture="built")
def build(checks):
    return protect.ruleset(_checks(checks))


def _rule(built, kind):
    return next(r for r in built["rules"] if r["type"] == kind)


@then("it targets the default branch and nobody may bypass it")
def default_branch(built):
    assert built["target"] == "branch" and built["enforcement"] == "active"
    assert built["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]
    assert built["bypass_actors"] == []


@then("it blocks deleting and force-pushing the branch")
def blocks(built):
    _rule(built, "deletion")
    _rule(built, "non_fast_forward")


@then("it takes changes only by pull request, with no approval needed")
def by_pull_request(built):
    assert _rule(built, "pull_request")["parameters"]["required_approving_review_count"] == 0


@then(parsers.parse('it requires "{a}" from GitHub Actions and "{b}" from SonarCloud'))
def requires(built, a, b):
    checks = _rule(built, "required_status_checks")["parameters"]["required_status_checks"]
    assert checks == [{"context": a, "integration_id": 15368},
                      {"context": b, "integration_id": 12526}]


@when("I read pubkit's protected.json", target_fixture="entries")
def read_protected():
    return json.loads((ROOT / "src" / "pubkit" / "protected.json").read_text())["repositories"]


@then("every repository in it requires at least one check, and is listed once")
def each_once(entries):
    repos = [e["repo"] for e in entries]
    assert len(repos) == len(set(repos)), repos
    for e in entries:
        assert e["checks"] and all(isinstance(c, str) and c for c in e["checks"]), e


@then("every repository in governed.json is in it")
def governed_protected(entries):
    governed = json.loads((ROOT / "governed.json").read_text())["repositories"]
    assert {g["repo"] for g in governed} <= {e["repo"] for e in entries}


@given(parsers.parse('protected.json lists "{slug}" requiring "{checks}"'))
def lists(repo, github, slug, checks):
    (repo / "protected.json").write_text(json.dumps(
        {"repositories": [{"repo": slug, "checks": _checks(checks)}]}))


@given(parsers.parse('GitHub has no ruleset in "{slug}"'))
def no_ruleset(github, slug):
    _edit(github, lambda s: s["rulesets"].__setitem__(slug, []))


@given(parsers.parse('GitHub has the ruleset "{name}" in "{slug}" requiring "{checks}"'))
def has_ruleset(github, name, slug, checks):
    rs = protect.ruleset(_checks(checks))
    rs.update(id=7, name=name)
    _edit(github, lambda s: s["rulesets"].__setitem__(slug, [rs]))


@given(parsers.parse('GitHub has pubkit\'s ruleset in "{slug}" requiring "{checks}"'))
def has_pubkit_ruleset(github, slug, checks):
    rs = protect.ruleset(_checks(checks))
    rs.update(id=7, source=slug, current_user_can_bypass="never")
    # Settings GitHub adds with its own default are no difference.
    _rule(rs, "pull_request")["parameters"]["require_extra_approval_for_unattributed_changes"] = True
    _edit(github, lambda s: s["rulesets"].__setitem__(slug, [rs]))


@given(parsers.parse('GitHub rejects the ruleset for "{slug}" because "{reason}"'))
def rejects(github, slug, reason):
    _edit(github, lambda s: s["reject"].__setitem__(slug, [reason]))


@given(parsers.parse('GitHub refuses to change rulesets in "{slug}"'))
def refuses(github, slug):
    _edit(github, lambda s: s["refuse"].append(slug))


def _sent(github):
    return json.loads(github.read_text())["sent"]


@then(parsers.re(r'GitHub was sent the ruleset for "(?P<slug>[^"]+)" requiring "(?P<checks>[^"]+)"'
                 r'(?:, replacing "(?P<old>[^"]+)")?$'))
def sent_ruleset(github, slug, checks, old):
    (call,) = _sent(github)
    assert call["body"] == protect.ruleset(_checks(checks))
    if old:
        assert (call["method"], call["url"]) == ("PUT", f"repos/{slug}/rulesets/7")
    else:
        assert (call["method"], call["url"]) == ("POST", f"repos/{slug}/rulesets")


@then("GitHub was sent nothing")
def sent_nothing(github):
    assert _sent(github) == []



@then("it prints nothing about a repository admin")
def no_admin_hint(result):
    out = result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout
    assert "repository admin" not in out, out
