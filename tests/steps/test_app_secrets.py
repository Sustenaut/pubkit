"""Steps for `pubkit app-secrets`, against a fake gh that logs its arguments and
what it read on its standard input."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from pytest_bdd import given, parsers, scenarios, then

scenarios("app-secrets.feature")

KEY = "-----BEGIN RSA PRIVATE KEY-----\nTOPSECRETKEYBODY\n-----END RSA PRIVATE KEY-----\n"
VALUES = {"THE_APP_ID": "5551234", "THE_APP_INSTALLATION_ID": "7770001", "THE_APP_KEY": KEY}
OTHER_APP = {"id": {"from": "other-app-id", "to": "OTHER_APP_ID"},
             "installation_id": {"from": "other-app-installation-id", "to": "OTHER_APP_INSTALLATION_ID"},
             "key": {"from": "other-app-key", "to": "OTHER_APP_KEY"}}
MORE = {"OTHER_APP_ID": "5551235", "OTHER_APP_INSTALLATION_ID": "7770002", "OTHER_APP_KEY": KEY.replace("BODY", "BODY2"),
        "PLAIN_TOKEN": "xoxb-plain-token-value", "PLAIN_USER": "U12345USER"}
BLOCK = {"id": {"from": "the-app-id", "to": "THE_APP_ID"},
         "installation_id": {"from": "the-app-installation-id", "to": "THE_APP_INSTALLATION_ID"},
         "key": {"from": "the-app-key", "to": "THE_APP_KEY"}}

FAKE_GH = f"""#!{sys.executable}
import json, os, sys
read = sys.stdin.read()
with open(os.environ["GH_LOG"], "a") as log:
    log.write(json.dumps({{"args": sys.argv[1:], "read": read}}) + "\\n")
if sys.argv[3] == os.environ.get("GH_REFUSE"):
    sys.stderr.write("gh: refused\\n")
    sys.exit(1)
"""


@pytest.fixture
def log(tmp_path, env):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    path = tmp_path / "gh.log"
    env["GH_LOG"] = str(path)
    env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    return path


def _calls(log):
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


@given(parsers.parse('a clone of "{slug}" whose github_app block names an app'))
def clone(repo, log, slug):
    (repo / "pubkit.json").write_text(json.dumps({"kind": "utility", "github_app": BLOCK}))
    subprocess.run(["git", "init", "-q", "."], cwd=repo, check=True)
    subprocess.run(["git", "remote", "add", "origin", f"https://github.com/{slug}.git"], cwd=repo,
                   check=True)


def _clone(repo, slug, config):
    (repo / "pubkit.json").write_text(json.dumps({"kind": "utility", **config}))
    subprocess.run(["git", "init", "-q", "."], cwd=repo, check=True)
    subprocess.run(["git", "remote", "add", "origin", f"https://github.com/{slug}.git"], cwd=repo, check=True)


@given(parsers.parse('a clone of "{slug}" that names two apps and two other values'))
def clone_many(repo, log, slug):
    _clone(repo, slug, {"github_app": [BLOCK, OTHER_APP], "secrets": [
        {"from": "plain-token", "to": "PLAIN_TOKEN", "kind": "secret"},
        {"from": "plain-user", "to": "PLAIN_USER", "kind": "variable"}]})


@given(parsers.parse('a clone of "{slug}" that names only "{name}" as a secret'))
def clone_one(repo, log, slug, name):
    _clone(repo, slug, {"secrets": [{"from": name, "to": "PLAIN_TOKEN", "kind": "secret"}]})


@given("Bitwarden has all those values")
def has_those(env):
    env.update(VALUES)
    env.update(MORE)


@given("Bitwarden has all three values")
def has_all(env):
    env.update(VALUES)


@given(parsers.parse("Bitwarden's \"{name}\" is {what}"))
def is_what(env, name, what):
    variable = name.upper().replace("-", "_")
    if what == "missing":
        del env[variable]
    else:
        env[variable] = "" if what == "empty" else what.strip('"')


@given(parsers.parse('gh refuses "{name}"'))
def refuses(env, name):
    env["GH_REFUSE"] = name


@given("its pubkit.json has a sound github_app block")
def sound(repo):
    config = json.loads((repo / "pubkit.json").read_text())
    config["github_app"] = BLOCK
    (repo / "pubkit.json").write_text(json.dumps(config))


@then(parsers.parse('gh set the {kind} "{name}" in "{slug}"'))
def gh_set(log, kind, name, slug):
    call = next(c for c in _calls(log) if c["args"][:3] == [kind, "set", name])
    assert call["args"][3:] == ["-R", slug]
    assert call["read"] == {**VALUES, **MORE}[name]


@then(parsers.parse("gh was called {count:d} times"))
def called(log, count):
    assert len(_calls(log)) == count


@then("no value is printed or on a command line")
def no_value(log, result):
    for value in {**VALUES, **MORE}.values():
        assert value.strip() not in result.stdout + result.stderr
        assert "TOPSECRETKEYBODY" not in result.stdout + result.stderr
        for call in _calls(log):
            assert value.strip() not in " ".join(call["args"])
