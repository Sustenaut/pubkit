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
    # ./with-secrets runs ./pubkit, which runs the release wheel through uvx:
    # this stand-in runs the pubkit under test with the same arguments.
    uvx = root / "bin" / "uvx"
    uvx.write_text(f'#!/bin/sh\n[ "$1 $3" = "--from pubkit" ] || exit 9\nshift 3\nexec "{_pubkit()}" "$@"\n')
    uvx.chmod(0o755)
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


@when(parsers.parse('I run ./with-secrets in the directory "{directory}" with the token "{token}"'),
      target_fixture="result")
def from_directory(repo, run, fake, directory, token):
    (repo / directory).mkdir(exist_ok=True)
    env = {"PATH": f"{fake / 'bin'}:{os.environ['PATH']}", "FAKE_BWS": str(fake),
           "SHOW_ENV": str(fake / "seen.env"), "BWS_ACCESS_TOKEN": token}
    return run(repo / "with-secrets", fake / "show", cwd=repo / directory, env=env)


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


FAKE_UVX = f"""#!{sys.executable}
import json, os, sys
json.dump({{"args": sys.argv[1:], "cwd": os.getcwd()}}, open(os.environ["FAKE_UVX"], "w"))
"""


@pytest.fixture
def launcher_env(tmp_path):
    """The environment ./with-secrets and ./pubsec run in: a cache of their own."""
    return {"XDG_CACHE_HOME": str(tmp_path / "cache")}


@given("uvx records how it was run")
def fake_uvx(tmp_path, launcher_env):
    bin_dir = tmp_path / "uvx-bin"
    bin_dir.mkdir()
    (bin_dir / "uvx").write_text(FAKE_UVX)
    (bin_dir / "uvx").chmod(0o755)
    launcher_env["FAKE_UVX"] = str(tmp_path / "uvx.json")
    launcher_env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


@pytest.fixture
def cached():
    return {}


@given(parsers.parse('deploy.providers lists a pubsec wheel in the cache, at "{version}"'))
def cached_wheel(repo, tmp_path, cached, version):
    """./pubsec as sync writes it for a pubkit.json whose deploy.providers lists
    pubsec at `version`, and that wheel already fetched into the cache, so
    nothing is downloaded."""
    import hashlib  # noqa: PLC0415

    from pubkit.cli import render, templates, values  # noqa: PLC0415
    data = b"a stand-in for pubsec's wheel"
    sha = hashlib.sha256(data).hexdigest()
    wheel = f"https://github.com/example-org/pubsec/releases/download/v{version}/pubsec-{version}-py3-none-any.whl"
    config = {"kind": "service", "deploy": {"providers": [{"distribution": "pubsec", "wheel": wheel, "sha256": sha}]}}
    (repo / "pubsec").write_text(render(templates("service")["pubsec"], values(config)))
    (repo / "pubsec").chmod(0o755)
    file = tmp_path / "cache" / "pubkit" / "providers" / sha / wheel.rsplit("/", 1)[1]
    file.parent.mkdir(parents=True)
    file.write_bytes(data)
    cached["file"] = file


@given("the cached wheel has changed")
def changed(cached):
    cached["file"].write_bytes(b"another wheel")


@when(parsers.re(r'I run ./with-secrets (?P<args>(?:"[^"]*" ?)+) in the directory "(?P<directory>[^"]*)"'),
      target_fixture="result")
def with_secrets_in(repo, run, launcher_env, args, directory):
    (repo / directory).mkdir(exist_ok=True)
    return run(repo / "with-secrets", *args.strip().strip('"').split('" "'), cwd=repo / directory, env=launcher_env)


@then(parsers.parse('uvx ran pubsec from the cached wheel with "{args}"'))
def ran_pubsec(repo, tmp_path, cached, args):
    seen = json.loads((tmp_path / "uvx.json").read_text())
    assert seen["args"] == ["--from", str(cached["file"]), "pubsec", *args.replace("{repo}", str(repo)).split()], seen
    assert seen["cwd"] == str(repo), seen


@then("uvx was not run")
def uvx_not_run(tmp_path):
    assert not (tmp_path / "uvx.json").exists()


@then(parsers.parse('"{path}" runs the oldest pubsec this pubkit works with'))
def runs_pinned(repo, path):
    from pubkit.new import PUBSEC_FLOOR, PUBSEC_WHEEL  # noqa: PLC0415
    text = (repo / path).read_text()
    assert f"fetch {PUBSEC_WHEEL.format(PUBSEC_FLOOR[0])} {PUBSEC_FLOOR[1]} providers deploy.providers\n" in text, text
    assert 'exec uvx --from "$fetched" pubsec "$@"' in text, text


@given("pubkit.json has a deploy block")
def deploy_block(repo):
    config = json.loads((repo / "pubkit.json").read_text())
    config["deploy"] = {"namespace": "example"}
    (repo / "pubkit.json").write_text(json.dumps(config, indent=2) + "\n")


@then(parsers.parse('"{path}" lists the pubsec and pubdeploy of deploy.providers by their wheels'))
def lists_provider(repo, path):
    listed = {p["distribution"]: p for p in json.loads((repo / "pubkit.json").read_text())["deploy"]["providers"]}
    assert set(listed) == {"pubsec", "pubdeploy"}
    lines = (repo / path).read_text().splitlines()
    assert f"pubsec @ {listed['pubsec']['wheel']}" in lines and f"pubdeploy @ {listed['pubdeploy']['wheel']}" in lines
