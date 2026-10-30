"""Steps for `pubkit protect`: the ruleset itself, the shipped
protected.json, and the command against a fake `gh` that keeps each
repository's rulesets in a JSON file and records what it was sent."""

from __future__ import annotations

import base64
import json
import os
import subprocess
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
f = re.match(r"repos/([^/]+/[^/]+)/contents/pubkit.json$", url)
if f:
    if f.group(1) not in state.get("files", {{}}):
        print(json.dumps({{"message": "Not Found"}}))
        sys.stderr.write("gh: Not Found (HTTP 404)\\n")
        sys.exit(1)
    print(json.dumps({{"content": state["files"][f.group(1)]}}))
    sys.exit(0)
r = re.match(r"repos/([^/]+/[^/]+)$", url)
if r and method == "PATCH":
    body = json.load(sys.stdin)
    state.setdefault("repos", {{}}).setdefault(
        r.group(1), {{"visibility": "public", "owner": {{"type": "Organization"}}}}).update(body)
    state["sent"].append({{"method": method, "url": url, "body": body}})
    json.dump(state, open(path, "w"))
    print("{{}}")
    sys.exit(0)
if r:
    print(json.dumps(state.get("repos", {{}}).get(
        r.group(1), {{"visibility": "public", "owner": {{"type": "Organization"}}}})))
    sys.exit(0)
o = re.match(r"orgs/([^/]+)$", url)
if o:
    if o.group(1) not in state.get("orgs", {{}}):
        print(json.dumps({{"message": "Not Found"}}))
        sys.stderr.write("gh: Not Found (HTTP 404)\\n")
        sys.exit(1)
    print(json.dumps(state["orgs"][o.group(1)]))
    sys.exit(0)
m = re.match(r"repos/([^/]+/[^/]+)/rulesets(?:/(\\d+))?", url)
repo, rid = m.group(1), m.group(2)
rulesets = state["rulesets"].setdefault(repo, [])
if method != "GET":
    if repo in state["refuse"]:
        sys.stderr.write("gh: Resource not accessible by integration (HTTP 403)\\n")
        sys.exit(1)
    body = json.load(sys.stdin)
    reasons = state["reject"].get(repo) or state["reject"].get(repo + " " + body["target"])
    if repo in state.get("no_queue", []) and any(r["type"] == "merge_queue" for r in body["rules"]):
        reasons = ["Invalid rule 'merge_queue': "]
    if reasons:
        print(json.dumps({{"message": "Validation Failed", "errors": reasons}}))
        sys.stderr.write("gh: Validation Failed (HTTP 422)\\n")
        sys.exit(1)
    state["sent"].append({{"method": method, "url": url, "body": body}})
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


@when(parsers.re(r'I build the ruleset requiring "(?P<checks>[^"]+)"'
                 r'(?P<queue>, without the merge queue)?$'), target_fixture="built")
def build(checks, queue):
    return protect.ruleset(_checks(checks), not queue)


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


@then("it requires a branch to be up to date with main before it merges")
def strict(built):
    # So the run on main after a merge repeats the pull request's, on the
    # same tree, and tagging a release needs no tests of its own.
    params = _rule(built, "required_status_checks")["parameters"]
    assert params["strict_required_status_checks_policy"] is True


@then("it merges through the merge queue, by merge commit, without asking the branch to be up to date")
def queued(built):
    # The queue tests the pull request merged into main as main will hold
    # it, which is what an up-to-date branch gave, without updating one.
    params = _rule(built, "merge_queue")["parameters"]
    assert params["merge_method"] == "MERGE" and params["grouping_strategy"] == "ALLGREEN"
    assert params["min_entries_to_merge"] == 1
    checks = _rule(built, "required_status_checks")["parameters"]
    assert checks["strict_required_status_checks_policy"] is False


@then("it has no merge queue")
def no_queue(built):
    assert "merge_queue" not in {r["type"] for r in built["rules"]}


@when("I build the tag ruleset", target_fixture="built")
def build_tags():
    return protect.tag_ruleset()


@then(parsers.parse('it targets the tags "{pattern}"'))
def tags(built, pattern):
    assert built["target"] == "tag" and built["enforcement"] == "active"
    assert built["conditions"]["ref_name"]["include"] == [pattern]


@then("it blocks creating, moving and deleting them")
def blocks_tags(built):
    assert {r["type"] for r in built["rules"]} == {"creation", "update", "deletion"}


@when(parsers.parse('I build the tag ruleset for "{slug}"'), target_fixture="built")
def build_tags_for(slug):
    return protect.tag_ruleset(slug)


@then("only GitHub Actions may bypass it")
def actions_bypass(built):
    assert built["bypass_actors"] == [
        {"actor_id": 15368, "actor_type": "Integration", "bypass_mode": "always"}]


@then("only the release app may bypass it")
def app_bypass(built):
    assert built["bypass_actors"] == [
        {"actor_id": protect.RELEASE_APP, "actor_type": "Integration", "bypass_mode": "always"}]
    assert protect.RELEASE_APP == 5529889


@when("I read pubkit's protected.json", target_fixture="entries")
def read_protected():
    return json.loads((ROOT / "protected.json").read_text())["repositories"]


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


@given(parsers.parse('protected.json lists "{slug}" requiring "{checks}", without tags'))
def lists_without_tags(repo, github, slug, checks):
    (repo / "protected.json").write_text(json.dumps(
        {"repositories": [{"repo": slug, "checks": _checks(checks), "tags": False}]}))


@given(parsers.parse('protected.json lists "{slug}" requiring "{checks}", without the merge queue'))
def lists_without_queue(repo, github, slug, checks):
    (repo / "protected.json").write_text(json.dumps(
        {"repositories": [{"repo": slug, "checks": _checks(checks), "queue": False}]}))


def _block(checks, tags):
    block = {"checks": _checks(checks)}
    if tags == "without":
        block["tags"] = False
    return {"kind": "service", "protect": block}


@given(parsers.re(r'"(?P<slug>[^"]+)"\'s pubkit.json on GitHub requires "(?P<checks>[^"]+)"'
                  r'(?:, (?P<tags>without) tags)?$'))
def remote_config(github, slug, checks, tags):
    content = base64.b64encode(json.dumps(_block(checks, tags)).encode()).decode()
    _edit(github, lambda s: s.setdefault("files", {}).__setitem__(slug, content))


@given(parsers.re(r'the working directory is a clone of "(?P<slug>[^"]+)" whose pubkit.json '
                  r'requires "(?P<checks>[^"]+)"(?:, (?P<tags>without) tags)?$'))
def local_config(repo, slug, checks, tags):
    (repo / "pubkit.json").write_text(json.dumps(_block(checks, tags)))
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                    f"git@github.com:{slug}.git"], check=True)


@given("its pubkit.json has no protect block")
def no_block(repo):
    (repo / "pubkit.json").write_text(json.dumps({"kind": "service"}))


@given(parsers.parse("its pubkit.json's protect block is {block}"))
def raw_block(repo, block):
    (repo / "pubkit.json").write_text(json.dumps({"kind": "service", "protect": json.loads(block)}))


@given(parsers.parse('its pubkit.json is "{text}"'))
def raw_local(repo, text):
    (repo / "pubkit.json").write_text(text)


@given(parsers.re(r'"(?P<slug>[^"]+)"\'s pubkit.json on GitHub is "(?P<text>.*)"$'))
def raw_remote(github, slug, text):
    content = base64.b64encode(text.encode()).decode()
    _edit(github, lambda s: s.setdefault("files", {}).__setitem__(slug, content))


@given(parsers.parse("protected.json is {text}"))
def raw_protected(repo, github, text):
    (repo / "protected.json").write_text(text)


@given(parsers.parse('GitHub has no ruleset in "{slug}"'))
def no_ruleset(github, slug):
    _edit(github, lambda s: s["rulesets"].__setitem__(slug, []))


def _tags():
    rs = protect.tag_ruleset()
    rs.update(id=8)
    return rs


@given(parsers.parse('GitHub has the ruleset "{name}" in "{slug}" requiring "{checks}"'))
def has_ruleset(github, name, slug, checks):
    rs = protect.ruleset(_checks(checks))
    rs.update(id=7, name=name)
    _edit(github, lambda s: s["rulesets"].__setitem__(slug, [rs, _tags()]))


def _pubkit_ruleset(slug, checks, queue=True):
    rs = protect.ruleset(_checks(checks), queue)
    rs.update(id=7, source=slug, current_user_can_bypass="never")
    # Settings GitHub adds with its own default are no difference.
    _rule(rs, "pull_request")["parameters"]["require_extra_approval_for_unattributed_changes"] = True
    return rs


@given(parsers.parse('GitHub has pubkit\'s ruleset in "{slug}" requiring "{checks}"'))
def has_pubkit_ruleset(github, slug, checks):
    _edit(github, lambda s: s["rulesets"].__setitem__(slug, [_pubkit_ruleset(slug, checks), _tags()]))


@given(parsers.parse('GitHub has pubkit\'s ruleset in "{slug}" requiring "{checks}", '
                     'without the merge queue'))
def has_unqueued_ruleset(github, slug, checks):
    _edit(github, lambda s: s["rulesets"].__setitem__(
        slug, [_pubkit_ruleset(slug, checks, queue=False), _tags()]))


@given(parsers.parse('GitHub has only main\'s ruleset in "{slug}" requiring "{checks}"'))
def has_main_only(github, slug, checks):
    _edit(github, lambda s: s["rulesets"].__setitem__(slug, [_pubkit_ruleset(slug, checks)]))


@given(parsers.parse('GitHub rejects the ruleset for "{slug}" because "{reason}"'))
def rejects(github, slug, reason):
    _edit(github, lambda s: s["reject"].__setitem__(slug, [reason]))


@given(parsers.parse('GitHub rejects the tag ruleset for "{slug}" because "{reason}"'))
def rejects_tags(github, slug, reason):
    _edit(github, lambda s: s["reject"].__setitem__(f"{slug} tag", [reason]))


@given(parsers.parse('"{slug}" is a {visibility} repository of an organization on the "{plan}" plan'))
def org_repo(github, slug, visibility, plan):
    def change(s):
        s.setdefault("repos", {})[slug] = {"visibility": visibility, "owner": {"type": "Organization"}}
        s.setdefault("orgs", {})[slug.split("/")[0]] = {"plan": {"name": plan}}
    _edit(github, change)


@given(parsers.parse('protected.json lists "{slug}" requiring "{checks}", with auto-merge'))
def lists_with_auto_merge(repo, github, slug, checks):
    (repo / "protected.json").write_text(json.dumps(
        {"repositories": [{"repo": slug, "checks": _checks(checks), "tags": False,
                           "auto_merge": True}]}))


@given(parsers.re(r'"(?P<slug>[^"]+)" (?P<on>allows|does not allow) auto-merge$'))
def auto_merge_setting(github, slug, on):
    _edit(github, lambda s: s.setdefault("repos", {}).setdefault(
        slug, {"visibility": "public", "owner": {"type": "Organization"}}).__setitem__(
        "allow_auto_merge", on == "allows"))


@then(parsers.parse('GitHub was asked to allow auto-merge in "{slug}"'))
def sent_auto_merge(github, slug):
    (call,) = [c for c in _sent(github) if "target" not in c["body"]]
    assert (call["method"], call["url"], call["body"]) == (
        "PATCH", f"repos/{slug}", {"allow_auto_merge": True})


@given(parsers.parse('"{slug}" is a public repository of a personal account'))
def user_repo(github, slug):
    _edit(github, lambda s: s.setdefault("repos", {}).__setitem__(
        slug, {"visibility": "public", "owner": {"type": "User"}}))


@given(parsers.parse('GitHub refuses the merge queue in "{slug}"'))
def refuses_queue(github, slug):
    _edit(github, lambda s: s.setdefault("no_queue", []).append(slug))


@given(parsers.parse('GitHub refuses to change rulesets in "{slug}"'))
def refuses(github, slug):
    _edit(github, lambda s: s["refuse"].append(slug))


def _sent(github):
    return json.loads(github.read_text())["sent"]


@then(parsers.re(r'GitHub was sent the ruleset for "(?P<slug>[^"]+)" requiring "(?P<checks>[^"]+)"'
                 r'(?:, replacing "(?P<old>[^"]+)")?(?P<queue>, without the merge queue)?$'))
def sent_ruleset(github, slug, checks, old, queue):
    (call,) = [c for c in _sent(github) if c["body"].get("target") == "branch"]
    assert call["body"] == protect.ruleset(_checks(checks), not queue)
    if old:
        assert (call["method"], call["url"]) == ("PUT", f"repos/{slug}/rulesets/7")
    else:
        assert (call["method"], call["url"]) == ("POST", f"repos/{slug}/rulesets")


@then(parsers.parse('GitHub was sent the tag ruleset for "{slug}"'))
def sent_tags(github, slug):
    (call,) = [c for c in _sent(github) if c["body"].get("target") == "tag"]
    assert (call["method"], call["url"], call["body"]) == (
        "POST", f"repos/{slug}/rulesets", protect.tag_ruleset(slug))


@then("GitHub was sent no tag ruleset")
def sent_no_tags(github):
    assert [c for c in _sent(github) if c["body"].get("target") == "tag"] == []


@then("GitHub was sent nothing")
def sent_nothing(github):
    assert _sent(github) == []



@then("it prints nothing about a repository admin")
def no_admin_hint(result):
    out = result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout
    assert "repository admin" not in out, out


@then("it prints nothing about a merge queue")
def no_queue_note(result):
    out = result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout
    assert "merge queue" not in out and "merge_queue" not in out, out


@then("it prints nothing about auto-merge")
def no_auto_merge_note(result):
    out = result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout
    assert "auto-merge" not in out, out
