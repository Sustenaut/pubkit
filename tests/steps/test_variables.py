"""Steps for `pubkit variables` and the state check of variables, against a fake
`gh` that answers from a JSON file, keeps what it is told to set, and logs every call."""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then

scenarios("variables.feature")

FAKE_GH = f"""#!{sys.executable}
import json, os, re, sys
path = os.environ["FAKE_GH"]
state = json.load(open(path))
args = sys.argv[1:]
read = sys.stdin.read() if args[0] == "variable" or "--input" in args else ""
with open(path + ".log", "a") as log:
    log.write(json.dumps({{"args": args, "read": read}}) + "\\n")
def save():
    json.dump(state, open(path, "w"))
def missing():
    print(json.dumps({{"message": "Not Found"}}))
    sys.stderr.write("gh: Not Found (HTTP 404)\\n")
    sys.exit(1)
if args[0] == "variable":
    state["variables"].setdefault(args[args.index("-R") + 1], {{}})[args[2]] = read
    save()
    sys.exit(0)
method, url = args[2], args[5]
v = re.match(r"repos/([^/]+/[^/]+)/actions/variables\\?per_page=(\\d+)&page=(\\d+)$", url)
if v:
    if v.group(1) in state["refuse"]:
        sys.stderr.write("gh: Resource not accessible by integration (HTTP 403)\\n")
        sys.exit(1)
    held = [{{"name": n, "value": x}} for n, x in state["variables"].get(v.group(1), {{}}).items()]
    size, page = int(v.group(2)), int(v.group(3))
    print(json.dumps({{"total_count": len(held), "variables": held[(page - 1) * size:page * size]}}))
    sys.exit(0)
d = re.match(r"repos/([^/]+/[^/]+)/actions/workflows/([^/]+)/dispatches$", url)
if d and method == "POST":
    state["ran"].append([d.group(1), d.group(2), json.loads(read)["ref"]])
    save()
    sys.exit(0)
f = re.match(r"repos/([^/]+/[^/]+)/contents/pubkit.json$", url)
if f:
    if f.group(1) not in state["files"]:
        missing()
    print(json.dumps({{"content": state["files"][f.group(1)]}}))
    sys.exit(0)
r = re.match(r"repos/([^/]+/[^/]+)$", url)
if r and r.group(1) in state["repos"]:
    print(json.dumps(state["repos"][r.group(1)]))
    sys.exit(0)
missing()
"""


@pytest.fixture(autouse=True)
def github(tmp_path, env):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    state = tmp_path / "github.json"
    state.write_text(json.dumps({"repos": {"example-org/two": {"default_branch": "main"}},
                                 "files": {}, "variables": {}, "refuse": [], "ran": []}))
    env["FAKE_GH"] = str(state)
    env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    return state


def _edit(github, change):
    state = json.loads(github.read_text())
    change(state)
    github.write_text(json.dumps(state))


def _calls(github):
    log = Path(f"{github}.log")
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def _config(github, slug, config):
    def change(state):
        state["repos"].setdefault(slug, {"default_branch": "main"})
        state["files"][slug] = base64.b64encode(json.dumps(config).encode()).decode()
    _edit(github, change)


@given(parsers.re(r'"(?P<slug>[^"]+)" declares "(?P<a>[^"]+)" and "(?P<b>[^"]+)", '
                  r'deploying with "(?P<deploy>[^"]+)"'))
def declares(github, slug, a, b, deploy):
    _config(github, slug, {"kind": "service", "variables": {"names": [a, b], "deploy": deploy}})


@given(parsers.re(r'"(?P<slug>[^"]+)" declares "(?P<a>[^"]+)" and "(?P<b>[^"]+)", without a deploy workflow'))
def declares_only(github, slug, a, b):
    _config(github, slug, {"kind": "service", "variables": {"names": [a, b]}})


@given(parsers.re(r'"(?P<slug>[^"]+)" has the pubkit.json "(?P<text>.+)"$'))
def has_config(github, slug, text):
    _config(github, slug, json.loads(text))


@given(parsers.re(r'the state gives "(?P<name>[^"]+)" the variables "(?P<text>.+)"$'))
def state_gives(repo, name, text):
    (repo / "state.json").write_text(json.dumps({"owner": "example-org", "repos": {
        name: {"board": True, "protect": False, "govern": False, "variables": json.loads(text)},
        "two": {"board": True, "protect": False, "govern": False}}}))


@given(parsers.re(r'GitHub holds "(?P<name>[^"]+)" in "(?P<slug>[^"]+)" as "(?P<value>.*)"$'))
def holds(github, name, slug, value):
    _edit(github, lambda state: state["variables"].setdefault(slug, {}).__setitem__(name, value))


@given(parsers.parse('GitHub refuses to show the variables of "{slug}"'))
def refuses(github, slug):
    _edit(github, lambda state: state["refuse"].append(slug))


@given(parsers.re(r"its pubkit.json's variables block is \"(?P<text>.+)\"$"))
def variables_block(repo, text):
    path = repo / "pubkit.json"
    config = json.loads(path.read_text())
    path.write_text(json.dumps({**config, "variables": json.loads(text)}))


@then(parsers.re(r'GitHub holds "(?P<name>[^"]+)" in "(?P<slug>[^"]+)" as "(?P<value>.*)" now$'))
def holds_now(github, name, slug, value):
    assert json.loads(github.read_text())["variables"][slug][name] == value


@then(parsers.parse('"{workflow}" ran on "{branch}" in "{slug}" {count:d} time'))
def ran(github, workflow, branch, slug, count):
    assert json.loads(github.read_text())["ran"] == [[slug, workflow, branch]] * count


@then("nothing was set or run")
def nothing_set(github):
    state = json.loads(github.read_text())
    assert state["ran"] == []
    assert not [c for c in _calls(github) if c["args"][0] == "variable" or "POST" in c["args"]]


@then("no value was on a command line")
def no_value_argv(github):
    for call in _calls(github):
        if call["args"][0] == "variable":
            assert call["read"] not in " ".join(call["args"])


@then("it does not succeed")
def not_ok(result):
    assert result.code != 0, result.stdout


@then(parsers.parse('it does not print "{text}"'))
def not_printed(result, text):
    assert text not in result.stdout, result.stdout


@then(parsers.parse('it warns "{text}"'))
def warns(result, text):
    assert text in result.stderr, result.stderr
