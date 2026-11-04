"""Steps for the service kind: `with-secrets` and `pubkit secrets run`, run as a
person would, against a stand-in for the bws CLI that serves fake secrets from a
JSON file."""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("service.feature")

BWS = """#!/bin/sh
[ "$BWS_ACCESS_TOKEN" = "tok" ] || { echo "invalid token" >&2; exit 1; }
case "$1 $2" in
"project list") cat "$FAKE_BWS/projects.json" ;;
"secret list") if [ "$3" = "$(cat "$FAKE_BWS/project_id")" ]; then cat "$FAKE_BWS/secrets.json"; else echo "[]"; fi ;;
*) exit 2 ;;
esac
"""

SHOW = """#!/bin/sh
env > "$SHOW_ENV"
pwd > "$SHOW_ENV.cwd"
"""
# What a shell sets for itself, so it differs between a bash script and pubkit,
# and what coverage sets in the Python processes it measures.
SHELL_OWN = {"_", "SHLVL", "OLDPWD"}


def _compared(name: str) -> bool:
    return name not in SHELL_OWN and not name.startswith("COVERAGE_")


def _pubkit():
    found = shutil.which("pubkit", path=str(Path(sys.executable).parent))
    assert found
    return found


@pytest.fixture
def fake(tmp_path):
    root = tmp_path / "fake"
    (root / "bin").mkdir(parents=True)
    bws = root / "bin" / "bws"
    bws.write_text(BWS)
    bws.chmod(0o755)
    show = root / "show"
    show.write_text(SHOW)
    show.chmod(0o755)
    (root / "projects.json").write_text("[]")
    (root / "project_id").write_text("")
    (root / "secrets.json").write_text("[]")
    return root


@given(parsers.parse('a repository initialised as service for the project "{project}"'))
def service_repo(repo, run, project):
    assert run(_pubkit(), "init", "--kind=service", f"--project={project}", cwd=repo).code == 0


@given(parsers.parse('a service whose Terraform declares "{variable}"'))
def declares(repo, run, variable):
    service_repo(repo, run, "infra")
    (repo / "variables.tf").write_text(f'variable "{variable}" {{\n  sensitive = true\n}}\n')


@given(parsers.parse('a service whose Terraform takes its secrets as a map of "{name}"'))
def as_map(repo, run, name):
    service_repo(repo, run, "infra")
    (repo / "infra").mkdir()
    (repo / "infra" / "secrets.tf").write_text(
        f'locals {{\n  secrets = {{\n    "{name}" = "a key",\n  }}\n}}\n')
    settings = json.loads((repo / "with-secrets.json").read_text())
    settings["terraform"] = {"dir": "infra", "secrets": "map", "names_from": "infra/secrets.tf"}
    (repo / "with-secrets.json").write_text(json.dumps(settings))


@given(parsers.parse('the aliases file "{path}" maps "{alias}" to "{secret}"'))
def aliases_file(repo, path, alias, secret):
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(json.dumps({"comment": "test", "aliases": {alias: secret}}))
    settings = json.loads((repo / "with-secrets.json").read_text())
    settings["aliases"] = [path]
    (repo / "with-secrets.json").write_text(json.dumps(settings))


def _holds(fake, project, pairs):
    (fake / "projects.json").write_text(json.dumps([{"id": f"id-{project}", "name": project}]))
    (fake / "project_id").write_text(f"id-{project}")
    when = "2026-10-13T11:00:00.123Z"
    (fake / "secrets.json").write_text(json.dumps(
        [{"key": key, "value": value, "revisionDate": when} for key, value in pairs]))


@given(parsers.parse('Bitwarden holds "{k1}" as "{v1}" and "{k2}" as "{v2}" in the project "{project}"'))
def holds(fake, k1, v1, k2, v2, project):
    _holds(fake, project, [(k1, v1), (k2, v2)])


@given(parsers.parse('Bitwarden holds "{k1}" as "{v1}" and "{k2}" as "{v2}" and "{k3}" as "{v3}" '
                     'in the project "{project}"'))
def holds_three(fake, k1, v1, k2, v2, k3, v3, project):
    _holds(fake, project, [(k1, v1), (k2, v2), (k3, v3)])


def _wrapper(repo, run, fake, env, runner, *command):
    env = {"PATH": f"{fake / 'bin'}:{os.environ['PATH']}", "FAKE_BWS": str(fake),
           "SHOW_ENV": str(fake / "seen.env"), **env}
    if runner == "./with-secrets":
        return run(repo / "with-secrets", *command, cwd=repo, env=env)
    program, *args = runner.split(" ")
    assert program == "pubkit", runner
    return run(_pubkit(), *args, "--", *command, cwd=repo, env=env)


@when(parsers.parse('I run "{runner}" with the token "{token}"'), target_fixture="result")
def with_token(repo, run, fake, runner, token):
    (repo / "infra").mkdir(exist_ok=True)
    return _wrapper(repo, run, fake, {"BWS_ACCESS_TOKEN": token}, runner, fake / "show")


@when(parsers.parse('I run "{runner}" with no token'), target_fixture="result")
def no_token(repo, run, fake, runner):
    return _wrapper(repo, run, fake, {"BWS_ACCESS_TOKEN": None}, runner, fake / "show")


@when(parsers.parse('I run "{runner}" with the token "{token}" and a command that exits {code:d}'),
      target_fixture="result")
def exits(repo, run, fake, runner, token, code):
    return _wrapper(repo, run, fake, {"BWS_ACCESS_TOKEN": token}, runner, "sh", "-c", f"exit {code}")


@when(parsers.parse('I run "{runner}" with the token "{token}" and the command "{command}"'),
      target_fixture="result")
def missing_command(repo, run, fake, runner, token, command):
    return _wrapper(repo, run, fake, {"BWS_ACCESS_TOKEN": token}, runner, command)


@given(parsers.parse('the repository has no "{path}"'))
def removed(repo, path):
    (repo / path).unlink()


@when(parsers.parse('I run both "{first}" and "{second}" with the token "{token}"'), target_fixture="both")
def run_both(repo, run, fake, first, second, token):
    out = []
    for runner in (first, second):
        result = _wrapper(repo, run, fake, {"BWS_ACCESS_TOKEN": token}, runner, fake / "show")
        out.append((result, {k: v for k, v in _seen(fake).items() if _compared(k)}))
        (fake / "seen.env").unlink()
    return out


@then("both succeed, print the same, and give the command the same environment")
def same(both):
    (first, first_env), (second, second_env) = both
    assert first.code == second.code == 0, (first.stderr, second.stderr)
    assert first.stderr == second.stderr
    assert first_env == second_env


@then(parsers.parse('the command ran in "{directory}"'))
def ran_in(repo, fake, directory):
    assert (fake / "seen.env.cwd").read_text().strip() == str(repo / directory)


def _seen(fake) -> dict[str, str]:
    out = {}
    for line in (fake / "seen.env").read_text().splitlines():
        key, _, value = line.partition("=")
        out[key] = value
    return out


@then(parsers.parse('the command sees "{name}" as "{value}"'))
def sees(fake, name, value):
    assert _seen(fake).get(name) == value, _seen(fake).get(name)


@then(parsers.parse('the command does not see "{name}"'))
def does_not_see(fake, name):
    assert name not in _seen(fake)


@then("the calling shell's environment is unchanged")
def unchanged():
    assert "TF_VAR_hcloud_token" not in os.environ and "RESTIC_PASSWORD" not in os.environ


@then("the command did not run")
def did_not_run(fake):
    assert not (fake / "seen.env").exists()


@then(parsers.parse('"{path}" is executable'))
def executable(repo, path):
    assert (repo / path).stat().st_mode & stat.S_IXUSR, path
