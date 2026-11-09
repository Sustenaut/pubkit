"""Steps for the repos file `pubkit protect --repos` reads, against a small
fake `gh` that answers from a JSON file and logs what it was asked."""

from __future__ import annotations

import base64
import json
import os
import sys

import pytest
from pytest_bdd import given, parsers, scenarios, then

scenarios("repos.feature")

FAKE_GH = f"""#!{sys.executable}
import json, os, re, sys
state = json.load(open(os.environ["FAKE_GH"]))
url = sys.argv[-1]
with open(os.environ["FAKE_GH"] + ".log", "a") as log:
    log.write(url + "\\n")
def missing():
    print(json.dumps({{"message": "Not Found"}}))
    sys.stderr.write("gh: Not Found (HTTP 404)\\n")
    sys.exit(1)
if re.search(r"/rulesets", url):
    print("[]")
    sys.exit(0)
reports = state.get("reports", {{}})
p = re.match(r"repos/([^/]+/[^/]+)/pulls\\?", url)
if p:
    if p.group(1) not in reports:
        missing()
    print(json.dumps([{{"head": {{"sha": "pr"}}}}] if "pr" in reports[p.group(1)] else []))
    sys.exit(0)
c = re.match(r"repos/([^/]+/[^/]+)/commits/([^/]+)/(check-runs|status)", url)
if c:
    found = reports.get(c.group(1), {{}}).get("pr" if c.group(2) == "pr" else "branch", {{}})
    if c.group(3) == "check-runs":
        print(json.dumps({{"check_runs": [{{"name": n}} for n in found.get("runs", [])]}}))
    else:
        print(json.dumps({{"statuses": [{{"context": n}} for n in found.get("statuses", [])]}}))
    sys.exit(0)
f = re.match(r"repos/([^/]+/[^/]+)/contents/pubkit.json$", url)
if f:
    if f.group(1) not in state["files"]:
        missing()
    print(json.dumps({{"content": state["files"][f.group(1)]}}))
    sys.exit(0)
r = re.match(r"repos/([^/]+/[^/]+)$", url)
if r and r.group(1) in state.get("refuse", []):
    sys.stderr.write("gh: Forbidden (HTTP 403)\\n")
    sys.exit(1)
if r and r.group(1) in state["repos"]:
    print(json.dumps(state["repos"][r.group(1)]))
    sys.exit(0)
missing()
"""


@pytest.fixture
def github(tmp_path, env):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    state = tmp_path / "github.json"
    state.write_text(json.dumps({"repos": {}, "files": {}}))
    env["FAKE_GH"] = str(state)
    env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    return state


def _edit(github, change):
    state = json.loads(github.read_text())
    change(state)
    github.write_text(json.dumps(state))


def _exists(state, slug):
    state["repos"].setdefault(slug, {"visibility": "public", "owner": {"type": "Organization"}})


@given(parsers.parse('the repos file marks "{one}" to protect, and "{two}" not'))
def repos_file(repo, github, one, two):
    (repo / "repos.json").write_text(json.dumps({"repositories": [
        {"repo": one, "protect": True, "govern": False},
        {"repo": two, "protect": False, "govern": True}]}))


@given(parsers.parse('"{slug}" has a protect block requiring "{check}"'))
def has_block(github, slug, check):
    text = json.dumps({"kind": "utility", "protect": {"checks": [check]}})

    def change(state):
        _exists(state, slug)
        state["files"][slug] = base64.b64encode(text.encode()).decode()
    _edit(github, change)


@then("it does not succeed")
def not_ok(result):
    assert result.code != 0, result.stdout


@then(parsers.parse('it does not print "{text}"'))
def not_printed(result, text):
    assert text not in result.stdout


@then(parsers.parse('it warns "{text}"'))
def warns(result, text):
    assert text in result.stderr, result.stderr


@then("it warns of nothing")
def no_warning(result):
    assert result.stderr == ""


@pytest.fixture(autouse=True)
def fake_github(github):
    """Every scenario here has the fake; none reaches GitHub."""
