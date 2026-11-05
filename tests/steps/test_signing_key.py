"""Steps for `pubkit signing-key backup`, end to end against a bws on PATH and a
stand-in bitwarden_sdk on PYTHONPATH, which log what they were given. HOME is a
directory of the scenario's own, so ~/ paths stay in it. Nothing reaches the
network.

A scenario runs in a session of its own, with no controlling terminal, so that
the hidden prompt reads its standard input wherever the suite is run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from conftest import pubkit
from pytest_bdd import given, parsers, scenarios, then, when

from pubkit.plugin import Completed

scenarios("signing-key.feature")

PROJECT_ID = "11111111-2222-3333-4444-555555555555"
ORGANIZATION_ID = "99999999-2222-3333-4444-555555555555"
CORPUS_KEY = "c0ffee" * 10 + "abcd\n"
HOME_KEY = "facade" * 10 + "0123\n"
TOKEN = "tok"

FAKE_BWS = f"""#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({{"tool": "bws", "args": args}}) + "\\n")
if args[:2] == ["project", "list"]:
    names = os.environ.get("BWS_PROJECTS", "the-project").split(",")
    print(json.dumps([{{"id": "{PROJECT_ID}" if i == 0 else f"id-{{i}}", "name": name, "organizationId": "{ORGANIZATION_ID}"}}
                      for i, name in enumerate(names)]))
elif args[:2] == ["secret", "list"]:
    print(os.environ.get("BWS_EXISTING", "[]"))
"""

FAKE_SDK = """
import json, os

class Answer:
    def __init__(self, success, error_message=None):
        self.success, self.error_message = success, error_message

def log(**record):
    with open(os.environ["FAKE_LOG"], "a") as out:
        out.write(json.dumps({"tool": "sdk", **record}) + "\\n")

class Auth:
    def login_access_token(self, token, state_file=None):
        return Answer(True)

class Secrets:
    def create(self, organization, key, value, note, projects=None):
        log(call="create", key=key, value=value, projects=[str(project) for project in projects])
        return Answer(True)

    def update(self, organization, secret, key, value, note, projects=None):
        log(call="update", key=key)
        return Answer(True)

class BitwardenClient:
    def auth(self):
        return Auth()

    def secrets(self):
        return Secrets()
"""


@pytest.fixture
def fakes(tmp_path, env):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "bws").write_text(FAKE_BWS)
    (bin_dir / "bws").chmod(0o755)
    sdk = tmp_path / "sdk" / "bitwarden_sdk"
    sdk.mkdir(parents=True)
    (sdk / "__init__.py").write_text(FAKE_SDK)
    (tmp_path / "home").mkdir()
    env.update(PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", PYTHONPATH=str(tmp_path / "sdk"),
               FAKE_LOG=str(tmp_path / "fakes.log"), HOME=str(tmp_path / "home"))
    return tmp_path


def _declare(repo, entries, key=CORPUS_KEY):
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "pubkit.json").write_text(json.dumps({"kind": "utility", "signing_keys": entries}))
    if key is not None:
        (repo / "corpus" / ".publet").mkdir(parents=True, exist_ok=True)
        (repo / "corpus" / ".publet" / "signing.key").write_text(key)


def _entry(path, name, project="the-project"):
    return {"path": path, "bitwarden": {"project": project, "name": name}}


@given(parsers.parse('a repository that declares its corpus key as "{name}" in the project "{project}"'))
def declares_corpus_key(repo, fakes, name, project):
    _declare(repo, [_entry("corpus/.publet/signing.key", name, project)])


@given(parsers.parse('a repository that declares its corpus key as "{name}" in the project with id "{uuid}" '
                     'named "{label}"'))
def declares_by_id(repo, fakes, name, uuid, label):
    _declare(repo, [{"path": "corpus/.publet/signing.key",
                     "bitwarden": {"project": {"id": uuid, "name": label}, "name": name}}])


@then(parsers.parse('it does not print "{text}"'))
def does_not_print(result, text):
    assert text not in result.stdout, result.stdout


@given(parsers.parse('a repository that declares "{path}" as "{name}" in the project "{project}"'))
def declares_home_key(repo, fakes, path, name, project):
    _declare(repo, [_entry(path, name, project)], key=None)
    target = fakes / "home" / path.removeprefix("~/")
    target.parent.mkdir(parents=True)
    target.write_text(HOME_KEY)


@given(parsers.parse('a repository that declares a key at "{path}" as "{name}"'))
def declares_missing(repo, fakes, path, name):
    _declare(repo, [_entry(path, name)], key=None)


@given("a repository with a corpus key it does not declare")
def undeclared(repo, fakes):
    _declare(repo, [])


@given(parsers.parse('two checkouts under one directory that declare their corpus keys as "{name}"'))
def two_checkouts(fakes, name):
    for checkout in ("one", "two"):
        _declare(fakes / "checkouts" / checkout, [_entry("corpus/.publet/signing.key", name)])


@given("the second checkout's corpus key differs")
def second_differs(fakes):
    (fakes / "checkouts" / "two" / "corpus" / ".publet" / "signing.key").write_text(HOME_KEY)
    (fakes / "fakes.log").unlink(missing_ok=True)


@given(parsers.parse('Bitwarden\'s projects are named "{names}"'))
def projects_named(env, names):
    env["BWS_PROJECTS"] = names


@given(parsers.parse('the project already holds the corpus key as "{name}"'))
def holds_same(env, name):
    env["BWS_EXISTING"] = json.dumps([{"id": "s1", "key": name, "value": CORPUS_KEY}])


@given(parsers.parse('the project already holds "{value}" as "{name}"'))
def holds_other(env, value, name):
    env["BWS_EXISTING"] = json.dumps([{"id": "s1", "key": name, "value": value}])


@given(parsers.parse("its pubkit.json's signing_keys is {block}"))
def block(repo, block):
    config = json.loads((repo / "pubkit.json").read_text())
    config["signing_keys"] = json.loads(block)
    (repo / "pubkit.json").write_text(json.dumps(config))


def _backup(cwd, env, token, *flags):
    argv = [pubkit(), "signing-key", "backup", *flags]
    done = subprocess.run(argv, input=f"{token}\n".encode(), cwd=cwd, env={**os.environ, **env},
                          capture_output=True, timeout=60, start_new_session=True, check=False)
    return Completed(argv, done.returncode, done.stdout, done.stderr)


@when(parsers.re(r'I back the keys up, giving the token "(?P<token>[^"]*)"'), target_fixture="result")
def back_up(repo, env, token):
    return _backup(repo, env, token)


@when(parsers.parse('I back up every repository under that directory, giving the token "{token}"'),
      target_fixture="result")
def back_up_under(fakes, env, token):
    return _backup(fakes, env, token, "--under", str(fakes / "checkouts"))


def _calls(fakes, tool):
    log = fakes / "fakes.log"
    lines = log.read_text().splitlines() if log.exists() else []
    return [json.loads(line) for line in lines if json.loads(line)["tool"] == tool]


@then("nothing was asked of Bitwarden")
def asked_nothing(fakes):
    assert not _calls(fakes, "bws") and not _calls(fakes, "sdk")


@then(parsers.parse('the SDK created "{name}" holding the {which} key'))
def created(fakes, name, which):
    made = [c for c in _calls(fakes, "sdk") if c["call"] == "create"]
    assert [(c["key"], c["value"], c["projects"]) for c in made] == [
        (name, CORPUS_KEY if which == "corpus" else HOME_KEY, [PROJECT_ID])]


@then(parsers.parse('the SDK created "{name}" once'))
def created_once(fakes, name):
    assert [c["key"] for c in _calls(fakes, "sdk") if c["call"] == "create"] == [name]


@then("the SDK wrote nothing")
def wrote_nothing(fakes):
    assert not _calls(fakes, "sdk")


@then("neither the key nor the token is in what it printed")
def nothing_secret(result):
    printed = result.stdout + result.stderr
    assert CORPUS_KEY.strip() not in printed
    assert f"{TOKEN}\n" not in printed and f" {TOKEN} " not in printed
