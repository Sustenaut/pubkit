"""Steps for how a walk reaches the cluster and the server: channels.py, with
a stand-in ssh on PATH that keeps its argv and standard input, and answers
as the server's read-only verb when it is asked for kubectl-read. The shared
steps are deploy_steps.py's."""

from __future__ import annotations

import json
import sys

from types import SimpleNamespace

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from deploy_steps import *  # noqa: F403 - the shared steps, and the fixtures pytest-bdd keeps them as
from deploy_world import CANARY
from pubkit.deploy.channels import channels
from pubkit.deploy.model import no_server

scenarios("deploy-channels.feature")

SSH = """#!{python}
import json, os, sys
kept = os.environ["SSH_KEPT"]
with open(os.path.join(kept, "argv"), "a") as out:
    out.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[-1] == "kubectl-read":
    for line in sys.stdin:
        with open(os.path.join(kept, "verb"), "a") as out:
            out.write(line)
        print(json.dumps({{"code": 0, "stdout": "{{\\"kind\\": \\"CronJob\\"}}", "stderr": ""}}), flush=True)
else:
    data = sys.stdin.buffer.read()
    with open(os.path.join(kept, "stdin"), "ab") as out:
        out.write(data)
    print("done")
"""


@pytest.fixture
def ssh(tmp_path, monkeypatch):
    """A stand-in ssh first on PATH, and the directory where it keeps what it got."""
    bin_dir, kept = tmp_path / "ssh-bin", tmp_path / "ssh-kept"
    bin_dir.mkdir()
    kept.mkdir()
    (bin_dir / "ssh").write_text(SSH.format(python=sys.executable))
    (bin_dir / "ssh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")
    monkeypatch.setenv("SSH_KEPT", str(kept))
    return SimpleNamespace(kept=kept, given={"kubectl": None, "server": None, "env": {}}, made=None, answers=[])


@given(parsers.re(r'the walk is given --server "(?P<host>[^"]+)"'))
def given_server(ssh, host):
    ssh.given["server"] = host


@given(parsers.re(r'the walk is given --kubectl "(?P<command>[^"]+)"'))
def given_kubectl(ssh, command):
    ssh.given["kubectl"] = command


@given(parsers.re(r'the walk is given the environment\'s (?P<name>[A-Z_]+) "(?P<value>[^"]+)"'))
def given_env(ssh, name, value):
    ssh.given["env"][name] = value


@given("the walk is given nothing")
def given_nothing(ssh):
    pass


@when(parsers.parse('I make the channels for the namespace "{ns}"'))
def make(ssh, ns):
    ssh.made = channels(ssh.given["kubectl"], ssh.given["server"], ssh.given["env"], ns)


@then(parsers.parse('the channel is "{name}"'))
def channel_is(ssh, name):
    assert ssh.made.name == name


@then(parsers.re(r"a command (?P<runs>runs|never runs) on the server"))
def server_runs(ssh, runs):
    assert (ssh.made.server is not no_server) == (runs == "runs")


MANIFEST = json.dumps({"kind": "Secret", "data": {"token": CANARY}}).encode()


@when("kubectl applies a Secret manifest holding a value")
def apply_manifest(ssh):
    ssh.answers.append(ssh.made.kubectl(["-n", "example", "apply", "-f", "-"], input=MANIFEST))


@when(parsers.parse('the server runs "{line}" with a script on its standard input'))
def server_runs_line(ssh, line):
    import shlex  # noqa: PLC0415
    ssh.answers.append(ssh.made.server(shlex.split(line), f"print('{CANARY}')".encode()))


@when(parsers.parse('kubectl gets the CronJob "{name}" twice'))
def get_twice(ssh, name):
    for _ in range(2):
        ssh.answers.append(ssh.made.kubectl(["-n", "example", "get", "cronjob", name, "-o", "json"]))
    ssh.made.kubectl.close()


def argvs(ssh) -> list[list[str]]:
    path = ssh.kept / "argv"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


CONTROL = ("ControlMaster=auto", "ControlPath=", "ControlPersist=60")


def without_control(argv: list[str]) -> tuple[list[str], list[str]]:
    """The argv without its -o Control... options, and those options."""
    kept, options, k = [], [], 0
    while k < len(argv):
        if argv[k] == "-o" and k + 1 < len(argv) and argv[k + 1].startswith("Control"):
            options.append(argv[k + 1])
            k += 2
            continue
        kept.append(argv[k])
        k += 1
    return kept, options


@then(parsers.parse('ssh was run as "{base}", over the walk\'s connection, and the line "{line}"'))
def ran_as(ssh, base, line):
    (argv,) = argvs(ssh)
    kept, options = without_control(argv)
    assert kept == [*base.split()[1:], line], argv
    assert [o.split("=")[0] for o in options] == [c.split("=")[0] for c in CONTROL], options


@then("both commands named the same ControlPath, in a directory of mode 0700")
def same_path(ssh):
    import os  # noqa: PLC0415
    paths = {o for argv in argvs(ssh) for o in without_control(argv)[1] if o.startswith("ControlPath=")}
    assert len(paths) == 1 and len(argvs(ssh)) == 2, argvs(ssh)
    ssh.socket_dir = os.path.dirname(paths.pop().split("=", 1)[1])
    assert os.stat(ssh.socket_dir).st_mode & 0o777 == 0o700


@when("the walk's connection is closed")
def close_connection(ssh):
    ssh.made.connection.close()


@then("ssh was asked to close it, and its directory is gone")
def closed(ssh):
    import os  # noqa: PLC0415
    assert argvs(ssh)[-1][-3:-1] == ["-O", "exit"], argvs(ssh)[-1]
    assert not os.path.exists(ssh.socket_dir)


@then("the value came on ssh's standard input, and in no argv")
def on_stdin(ssh):
    assert CANARY.encode() in (ssh.kept / "stdin").read_bytes()
    assert not any(CANARY in json.dumps(a) for a in argvs(ssh))


@then(parsers.parse('ssh was started once, as "{command}"'))
def started_once(ssh, command):
    assert argvs(ssh) == [command.split()[1:]], argvs(ssh)


@then(parsers.parse("the verb was sent {call} twice"))
def verb_sent(ssh, call):
    assert [json.loads(line) for line in (ssh.kept / "verb").read_text().splitlines()] == [json.loads(call)] * 2


@then("kubectl's answer was the verb's")
def answer_verbs(ssh):
    assert all(a.returncode == 0 and json.loads(a.stdout) == {"kind": "CronJob"} for a in ssh.answers)


@then(parsers.parse('kubectl\'s answer is exit {code:d}, "{text}"'))
def answer_is(ssh, code, text):
    assert ssh.answers[-1].returncode == code and text in ssh.answers[-1].stderr, ssh.answers[-1]


@then("ssh was never run")
def never_run(ssh):
    assert argvs(ssh) == []


@then("kubectl asked for no Secret's data")
def no_secret_data(world):
    for call in world.calls["kubectl"]:
        if "secret" in call and "get" in call:
            assert call[call.index("-o") + 1] == "name", call



