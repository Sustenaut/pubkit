"""Steps for the repos file and `pubkit state --check`, against a small fake
`gh` that answers from a JSON file and logs what it was asked."""

from __future__ import annotations

import base64
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("repos.feature")

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "board"
WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"

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
    has_config(github, slug, text)


@given(parsers.parse('"{slug}" has the pubkit.json "{text}"'))
def has_config(github, slug, text):
    def change(state):
        _exists(state, slug)
        state["files"][slug] = base64.b64encode(text.encode()).decode()
    _edit(github, change)


@given(parsers.parse('"{slug}" exists'))
def exists(github, slug):
    _edit(github, lambda state: _exists(state, slug))


@given(parsers.parse('"{slug}" is now "{moved}"'))
def moved_to(github, slug, moved):
    _edit(github, lambda state: state["repos"].__setitem__(slug, {"full_name": moved}))


@given(parsers.parse('GitHub refuses "{slug}"'))
def refuses(github, slug):
    _edit(github, lambda state: state.setdefault("refuse", []).append(slug))


@given(parsers.parse('protected.json lists "{slug}"'))
def protected_lists(repo, github, slug):
    (repo / "protected.json").write_text(json.dumps(
        {"repositories": [{"repo": slug, "checks": ["ci / passed"]}]}))


@given(parsers.re(r'the state of "(?P<owner>[^"]+)" has "(?P<one>[^"]+)" with protect (?P<a>on|off) '
                  r'and "(?P<two>[^"]+)" with protect (?P<b>on|off)'))
def state_file(repo, github, owner, one, a, two, b):
    (repo / "state.json").write_text(json.dumps({"owner": owner, "repos": {
        one: {"project": "pub", "board": True, "protect": a == "on", "govern": False},
        two: {"board": True, "protect": b == "on", "govern": True}}}))


@given("the build recording, and a repos file with projects")
def recording_with_repos(repo):
    shutil.copy(FIXTURES / "recorded.json", repo / "recorded.json")
    (repo / "repos.json").write_text(json.dumps({"repositories": [
        {"repo": "example-org/pubthing", "protect": True, "govern": True, "project": "pub"},
        {"repo": "example-org/site-thing", "protect": False, "govern": False}]}))


@given("the build recording, protected.json and governed.json in the repository")
def recording_with_lists(repo):
    for name in ("recorded.json", "protected.json", "governed.json"):
        shutil.copy(FIXTURES / name, repo / name)


@when(parsers.parse('I parse the workflow "{name}"'), target_fixture="workflow")
def parse_workflow(name):
    return {"data": yaml.safe_load((WORKFLOWS / name).read_text())}


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


@then("GitHub was asked nothing")
def asked_nothing(env):
    assert not Path(env["FAKE_GH"] + ".log").exists()


@pytest.fixture(autouse=True)
def fake_github(github):
    """Every scenario here has the fake; none reaches GitHub."""


def _row(repo, path, slug):
    board = json.loads((repo / path).read_text())
    return next(r for r in board["repos"] if r["repo"] == slug)


@then(parsers.parse('"{path}" lists "{slug}" in the project "{project}"'))
def in_project(repo, path, slug, project):
    assert _row(repo, path, slug)["project"] == project


@then(parsers.parse('"{path}" says "{slug}" has no protection to differ'))
def no_protection(repo, path, slug):
    row = _row(repo, path, slug)
    assert row["facts"]["protection"]["value"] == {"differences": [], "limit": False}
    assert "project" not in row


@then(parsers.parse('"{path}" says "{slug}" is governed'))
def governed(repo, path, slug):
    assert _row(repo, path, slug)["facts"]["nightly"]["value"]["governed"] is True


@then(parsers.parse('"{path}" names its calls under the connector "{name}"'))
def connector(repo, path, name):
    calls = json.loads((repo / path).read_text())["calls"]
    assert [c for c in calls if c["connector"] == name and c["method"] == "listed"]
    assert not [c for c in calls if c["connector"] == "lists"]


@then(parsers.parse('"{path}" shows "{slug}" in the project "{project}"'))
def shows_project(repo, path, slug, project):
    assert f"{slug} <span class=tag>{project}</span>" in (repo / path).read_text()


@then(parsers.parse('it passes the "{name}" input to pubkit board as a file, and otherwise the old lists'))
def board_input(workflow, name):
    assert name in workflow["data"][True]["workflow_call"]["inputs"]
    run = next(s for s in workflow["data"]["jobs"]["board"]["steps"] if "pubkit board" in s.get("run", ""))
    assert "--repos .pubkit-repos.json" in run["run"] and '--config "$PROTECTED"' in run["run"]
    assert run["env"]["REPOS"] == "${{ inputs.repos }}"
