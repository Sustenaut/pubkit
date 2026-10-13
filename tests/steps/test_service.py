"""Steps for the service kind: `with-secrets`, run as a person would, against
a stand-in for the bws CLI that serves fake secrets from a JSON file."""

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
"""


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


@given(parsers.parse('Bitwarden holds "{k1}" as "{v1}" and "{k2}" as "{v2}" in the project "{project}"'))
def holds(fake, k1, v1, k2, v2, project):
    (fake / "projects.json").write_text(json.dumps([{"id": f"id-{project}", "name": project}]))
    (fake / "project_id").write_text(f"id-{project}")
    when = "2026-10-13T11:00:00.123Z"
    (fake / "secrets.json").write_text(json.dumps(
        [{"key": k1, "value": v1, "revisionDate": when}, {"key": k2, "value": v2, "revisionDate": when}]))


def _wrapper(repo, run, fake, env, *command):
    env = {"PATH": f"{fake / 'bin'}:{os.environ['PATH']}", "FAKE_BWS": str(fake),
           "SHOW_ENV": str(fake / "seen.env"), **env}
    return run(repo / "with-secrets", *command, cwd=repo, env=env)


@when(parsers.parse('I run "./with-secrets" with the token "{token}"'), target_fixture="result")
def with_token(repo, run, fake, token):
    return _wrapper(repo, run, fake, {"BWS_ACCESS_TOKEN": token}, fake / "show")


@when('I run "./with-secrets" with no token', target_fixture="result")
def no_token(repo, run, fake):
    return _wrapper(repo, run, fake, {"BWS_ACCESS_TOKEN": None}, fake / "show")


@when(parsers.parse('I run "./with-secrets" with the token "{token}" and a command that exits {code:d}'),
      target_fixture="result")
def exits(repo, run, fake, token, code):
    return _wrapper(repo, run, fake, {"BWS_ACCESS_TOKEN": token}, "sh", "-c", f"exit {code}")


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
