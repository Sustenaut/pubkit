"""Steps for `pubkit service-account-key create`, end to end against fakes on PATH
for gcloud, bws and uv, and a stand-in bitwarden_sdk on PYTHONPATH. Each logs what it
was given, so a scenario can say what held the key. Nothing reaches the network.

A scenario runs in a session of its own, with no controlling terminal, so that
the hidden prompt reads its standard input wherever the suite is run.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys

import pytest
from conftest import pubkit
from pytest_bdd import given, parsers, scenarios, then, when

from pubkit.plugin import Completed

scenarios("service-account-key.feature")

ACCOUNT = "the-account@the-google-project.iam.gserviceaccount.com"
PROJECT_ID = "11111111-2222-3333-4444-555555555555"
ORGANIZATION_ID = "99999999-2222-3333-4444-555555555555"
SECRET_ID = "22222222-2222-3333-4444-555555555555"
KEY = json.dumps({"type": "service_account", "private_key_id": "new-key-id",
                  "private_key": "-----BEGIN PRIVATE KEY-----\nthe-private-key-body\n-----END PRIVATE KEY-----\n"})

FAKE_GCLOUD = f"""#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({{"tool": "gcloud", "args": args}}) + "\\n")
if "create" in args:
    sys.stderr.write("Created key [new-key-id] of type [json]\\n")
    print({KEY!r})
elif "list" in args:
    print("new-key-id\\nold-key-id")
"""

FAKE_BWS = f"""#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({{"tool": "bws", "args": args}}) + "\\n")
if args[:2] == ["project", "list"]:
    print(json.dumps([{{"id": "{PROJECT_ID}", "name": "the-project", "organizationId": "{ORGANIZATION_ID}"}}]))
elif args[:2] == ["secret", "list"]:
    print(os.environ.get("BWS_EXISTING", "[]"))
"""

FAKE_UV = f"""#!{sys.executable}
import json, os, shutil, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({{"tool": "uv", "args": args}}) + "\\n")
if os.environ.get("UV_REFUSES"):
    sys.stderr.write("no wheel for this platform\\n")
    sys.exit(1)
shutil.copytree(os.environ["FAKE_SDK"], os.path.join(args[args.index("--target") + 1], "bitwarden_sdk"))
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
        log(call="login", token=token)
        return Answer(True)

class Secrets:
    def _answer(self, value):
        if os.environ.get("SDK_REFUSES"):
            return Answer(False, "403 for " + value)
        return Answer(True)

    def create(self, organization, key, value, note, projects=None):
        log(call="create", organization=str(organization), key=key, value=value, note=note,
            projects=[str(project) for project in projects])
        return self._answer(value)

    def update(self, organization, secret, key, value, note, projects=None):
        log(call="update", organization=str(organization), secret=str(secret), key=key, value=value,
            note=note, projects=[str(project) for project in projects])
        return self._answer(value)

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
    for name, text in (("gcloud", FAKE_GCLOUD), ("bws", FAKE_BWS), ("uv", FAKE_UV)):
        path = bin_dir / name
        path.write_text(text)
        path.chmod(0o755)
    sdk = tmp_path / "sdk" / "bitwarden_sdk"
    sdk.mkdir(parents=True)
    (sdk / "__init__.py").write_text(FAKE_SDK)
    env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    env["PYTHONPATH"] = str(tmp_path / "sdk")
    env["FAKE_LOG"] = str(tmp_path / "fakes.log")
    env["FAKE_SDK"] = str(sdk)
    return tmp_path


@given("a service account with another key")
def account(repo, fakes):
    (repo / "with-secrets.json").write_text(json.dumps({"project": "the-project"}))


@given(parsers.parse('the project already has the secret "{name}"'))
def existing(env, name):
    env["BWS_EXISTING"] = json.dumps([{"id": SECRET_ID, "key": name, "value": "the-old-value"}])


@given("Bitwarden refuses writes, quoting what it was given")
def refuses(env):
    env["SDK_REFUSES"] = "1"


@given("the SDK is not installed")
def no_sdk(env):
    del env["PYTHONPATH"]


@given("uv refuses to install it")
def uv_refuses(env):
    env["UV_REFUSES"] = "1"


def _make(repo, env, flags, lines, address=ACCOUNT):
    argv = [pubkit(), "service-account-key", "create", f"--account={address}", "--secret=the-key", *flags]
    done = subprocess.run(argv, input="".join(f"{line}\n" for line in lines).encode(), cwd=repo,
                          env={**os.environ, **env}, capture_output=True, timeout=60,
                          start_new_session=True, check=False)
    return Completed(argv, done.returncode, done.stdout, done.stderr)


@when(parsers.parse('I make the key, giving the token "{token}" and answering "{answer}"'), target_fixture="result")
def make(repo, env, token, answer):
    return _make(repo, env, [], [token, answer])


@when("I make the key, giving no token", target_fixture="result")
def make_no_token(repo, env):
    return _make(repo, env, [], ["", "yes"])


@when(parsers.parse('I make the key with "{flag}", giving the token "{token}" and answering "{answer}"'),
      target_fixture="result")
def make_flag(repo, env, flag, token, answer):
    return _make(repo, env, [flag], [token, answer])


@when(parsers.parse('I make the key with "{flag}", giving nothing'), target_fixture="result")
def make_nothing(repo, env, flag):
    return _make(repo, env, [flag], [])


@when(parsers.parse('I make the key for "{address}", giving the token "{token}" and answering "{answer}"'),
      target_fixture="result")
def make_for(repo, env, address, token, answer):
    return _make(repo, env, [], [token, answer], address)


def calls(tmp_path, tool):
    log = tmp_path / "fakes.log"
    records = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return [record for record in records if record["tool"] == tool]


@then("gcloud made one key, on stdout")
def made_key(tmp_path):
    made = [call["args"] for call in calls(tmp_path, "gcloud") if "create" in call["args"]]
    assert len(made) == 1
    assert made[0][made[0].index("create") + 1] == "-"
    assert f"--iam-account={ACCOUNT}" in made[0]
    assert "--project=the-google-project" in made[0]


@then("gcloud was not called")
def no_gcloud(tmp_path):
    assert calls(tmp_path, "gcloud") == []


@then("gcloud deleted the new key")
def deleted(tmp_path):
    assert any("delete" in call["args"] and "new-key-id" in call["args"] for call in calls(tmp_path, "gcloud"))


@then(parsers.parse("Bitwarden's SDK created \"{name}\" with the key, base64-encoded, in the project"))
def sdk_created(tmp_path, name):
    wrote = [call for call in calls(tmp_path, "sdk") if call["call"] == "create"]
    assert len(wrote) == 1
    assert wrote[0]["key"] == name
    assert json.loads(base64.b64decode(wrote[0]["value"])) == json.loads(KEY)
    assert wrote[0]["organization"] == ORGANIZATION_ID
    assert wrote[0]["projects"] == [PROJECT_ID]


@then(parsers.parse("Bitwarden's SDK updated the existing \"{name}\" with the key"))
def sdk_updated(tmp_path, name):
    wrote = [call for call in calls(tmp_path, "sdk") if call["call"] != "login"]
    assert [call["call"] for call in wrote] == ["update"]
    assert wrote[0]["secret"] == SECRET_ID
    assert wrote[0]["key"] == name
    assert json.loads(base64.b64decode(wrote[0]["value"])) == json.loads(KEY)


@then(parsers.parse('the SDK logged in with the token "{token}"'))
def logged_in(tmp_path, token):
    assert [call["token"] for call in calls(tmp_path, "sdk") if call["call"] == "login"] == [token]


@then("no command line held the token or the key")
def no_command_line(tmp_path):
    encoded = base64.b64encode(KEY.encode()).decode()
    for tool in ("gcloud", "bws", "uv"):
        for call in calls(tmp_path, tool):
            for arg in call["args"]:
                assert "write-token" not in arg
                assert encoded[:40] not in arg
                assert "the-private-key-body" not in arg


@then("neither the key nor the token is printed")
def not_printed(result):
    printed = result.stdout + result.stderr
    assert "the-private-key-body" not in printed
    assert base64.b64encode(KEY.encode()).decode()[:40] not in printed
    assert "write-token" not in printed


@then("no file under the repository holds the key")
def not_on_disk(repo):
    encoded = base64.b64encode(KEY.encode()).decode()[:40]
    for path in repo.rglob("*"):
        if path.is_file():
            text = path.read_text(errors="replace")
            assert "the-private-key-body" not in text, path
            assert encoded not in text, path


@then("uv installed the SDK with --require-hashes")
def by_hash(tmp_path):
    installed = calls(tmp_path, "uv")
    assert len(installed) == 1
    assert "--require-hashes" in installed[0]["args"]
    assert installed[0]["args"][installed[0]["args"].index("-r") + 1].endswith("bitwarden-sdk.lock")
