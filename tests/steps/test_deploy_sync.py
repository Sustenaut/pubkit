"""What `pubkit sync` and `pubkit bump` do for a deploy: the managed
with-secrets in every repository with a with-secrets.json, the launcher's
provider wheels, the scheduled walk's caller, and the deploy trigger from
k8s-deploy.yml 0.31.0. Fixture workflows only, in a scratch repository."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess

import pytest

from deploy_world import FIXTURE
from pubkit import cli
from pubkit.deploy import providers
from pubkit.deploy.units import retrigger, trigger_problems

OLD_SCRIPT = "#!/usr/bin/env bash\nexec bws run -- \"$@\"\n"
PUSHED = """name: deploy
on:
  push:
    branches: [main]
  workflow_dispatch: {}
permissions:
  contents: read
  packages: write
jobs:
  deploy:
    uses: example-org/pubkit/.github/workflows/k8s-deploy.yml@v0.30.0
    with:
      image: ghcr.io/example-org/example
      deploys: |
        k8s example
    secrets: inherit
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setenv("PUBKIT_ANY_VERSION", "1")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def init(root, kind, **extra):
    assert cli.main(["init", f"--kind={kind}", *(["--project=example"] if kind == "service" else [])]) == 0
    config = json.loads((root / "pubkit.json").read_text())
    (root / "pubkit.json").write_text(json.dumps(config | extra, indent=2))


def test_a_utility_with_a_with_secrets_json_gets_the_managed_with_secrets(repo, capsys):
    init(repo, "utility")
    (repo / "with-secrets.json").write_text('{"project": "example"}\n')
    (repo / "with-secrets").write_text(OLD_SCRIPT)
    assert cli.main(["sync", "--check"]) == 1
    err = capsys.readouterr().err
    assert ("with-secrets differs from pubkit" in err and "it is an older script, which does not say which Bitwarden "
            "project it read (PUBKIT_SECRETS_PROJECT), so pubkit deploy finds every bitwarden.* need unknown "
            "through it") in err
    assert cli.main(["sync"]) == 0
    assert "Managed by pubkit" in (repo / "with-secrets").read_text()
    assert (repo / "with-secrets").stat().st_mode & 0o111
    capsys.readouterr()
    assert cli.main(["sync", "--check"]) == 0


def test_a_utility_without_one_gets_none(repo, capsys):
    init(repo, "utility")
    assert cli.main(["sync"]) == 0
    assert not (repo / "with-secrets").exists()


def deploy_repo(root):
    shutil.copytree(FIXTURE / "k8s", root / "k8s")
    shutil.copytree(FIXTURE / ".github", root / ".github", dirs_exist_ok=True)
    return json.loads((FIXTURE / "pubkit.json").read_text())["deploy"]


def test_sync_seeds_the_scheduled_walk_once(repo, capsys, monkeypatch):
    init(repo, "service")
    deploy = deploy_repo(repo)
    config = json.loads((repo / "pubkit.json").read_text())
    (repo / "pubkit.json").write_text(json.dumps(config | {"deploy": deploy}, indent=2))
    monkeypatch.setattr("pubkit.deploy.offline_lines", lambda root, config: ([], False))
    cli.main(["sync", "--check"])
    assert "no .github/workflows/deploy-check.yml, the scheduled deploy walk: `pubkit sync` seeds it" in \
        capsys.readouterr().err
    cli.main(["sync"])
    caller = repo / ".github" / "workflows" / "deploy-check.yml"
    text = caller.read_text()
    assert "uses: Sustenaut/pubkit/.github/workflows/deploy-check.yml@v" in text and "secrets: inherit" in text
    caller.write_text(text + "# the repository's own\n")
    cli.main(["sync"])
    assert caller.read_text().endswith("# the repository's own\n")


WHEEL = "https://github.com/example-org/example-needs/releases/download/v1.0.0/example_needs-1.0.0-py3-none-any.whl"


def launcher(config):
    return cli.render(cli.templates("utility")["pubkit"], cli.values(config))


def test_the_launcher_runs_with_each_provider_checked_against_its_sha256(tmp_path):
    wheel = b"a wheel"
    digest = hashlib.sha256(wheel).hexdigest()
    text = launcher({"deploy": {"providers": [{"distribution": "example-needs", "wheel": WHEEL, "sha256": digest}]}})
    assert f"provider {WHEEL} {digest}" in text and '"${with[@]}" pubkit "$@"' in text
    cache = tmp_path / "cache"
    held = cache / "pubkit" / "providers" / digest / WHEEL.rsplit("/", 1)[1]
    held.parent.mkdir(parents=True)
    held.write_bytes(wheel)
    part = text[text.index("# The packages"):text.index("exec uvx")]
    script = f"set -euo pipefail\n{part}printf '%s\\n' \"${{with[@]}}\"\n"
    done = subprocess.run(["bash", "-c", script], env={"PATH": "/usr/bin:/bin", "XDG_CACHE_HOME": str(cache)},
                          capture_output=True, text=True)
    assert done.returncode == 0 and done.stdout.split() == ["--with", str(held)], done
    held.write_bytes(b"another wheel")
    done = subprocess.run(["bash", "-c", script], env={"PATH": "/usr/bin:/bin", "XDG_CACHE_HOME": str(cache)},
                          capture_output=True, text=True)
    assert done.returncode == 1 and "is not the wheel deploy.providers pins" in done.stderr
    assert not held.exists()


def test_the_launcher_without_providers_is_as_it_was():
    text = launcher({})
    assert "provider" not in text and 'py3-none-any.whl" pubkit "$@"' in text


def test_bump_moves_a_provider_to_its_latest_release(repo):
    newer = WHEEL.replace("1.0.0", "1.1.0")
    (repo / "pubkit.json").write_text(json.dumps({"kind": "service", "deploy": {"namespace": "example", "providers": [
        {"distribution": "example-needs", "wheel": WHEEL, "sha256": "0" * 64}]}}, indent=2))

    def gh(method, path, body):
        assert (method, path) == ("GET", "repos/example-org/example-needs/releases/latest")
        return {"assets": [{"name": "example_needs-1.1.0.tar.gz", "browser_download_url": "x"},
                           {"name": "example_needs-1.1.0-py3-none-any.whl", "browser_download_url": newer,
                            "digest": "sha256:" + "1" * 64}]}
    assert providers.bump(repo, gh) == ["updated  pubkit.json (deploy.providers example-needs: 1.1.0)"]
    given = json.loads((repo / "pubkit.json").read_text())["deploy"]["providers"][0]
    assert given == {"distribution": "example-needs", "wheel": newer, "sha256": "1" * 64}
    assert providers.bump(repo, lambda *_: {"assets": [{"name": "example_needs-1.1.0-py3-none-any.whl",
                                                       "browser_download_url": newer, "digest": "sha256:1"}]}) == []


def test_a_type_no_installed_package_offers_names_the_providers():
    from pubkit.deploy.registry import types  # noqa: PLC0415
    from pubkit.deploy.schema import need_problems  # noqa: PLC0415
    assert need_problems({"type": "example.database"}, 0, types(), FIXTURE, ["example-needs"]) == [
        "deploy: needs[0]: example.database is no type of need pubkit or an installed package offers; it would be "
        "example-needs's, of deploy.providers, which ./pubkit runs with: run ./pubkit, or install example-needs"]


def test_the_trigger_rewrite_satisfies_the_check():
    moved = PUSHED.replace("@v0.30.0", "@v0.31.1")
    assert trigger_problems(moved, "deploy.yml")
    rewritten = retrigger(moved)
    assert trigger_problems(rewritten, "deploy.yml") == []
    assert "push:" not in rewritten and "branches: [main]\n  workflow_dispatch" not in rewritten
    assert "  workflow_dispatch: {}\n" in rewritten and "  actions: read\n  contents: read\n" in rewritten


@pytest.mark.parametrize("text", [
    "on: push\npermissions: { contents: read }\njobs: {}\n",
    "on: [push, workflow_dispatch]\njobs: {}\n",
])
def test_the_trigger_rewrite_takes_the_short_forms(text):
    rewritten = retrigger(text.replace("jobs: {}", "jobs:\n  d:\n    uses: example-org/pubkit/.github/workflows/"
                                                   "k8s-deploy.yml@v0.31.1"))
    assert trigger_problems(rewritten, "deploy.yml") == [], rewritten


def test_bump_rewrites_the_trigger_when_it_moves_the_pin_across_0_31(repo, monkeypatch, capsys):
    from pubkit import freshness  # noqa: PLC0415
    (repo / "pubkit.json").write_text('{"kind": "service"}\n')
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "deploy.yml").write_text(PUSHED)
    monkeypatch.setattr(cli, "sync", lambda root, check: 0)
    monkeypatch.setattr(cli, "lock", lambda root: 0)
    monkeypatch.setattr(freshness, "__version__", "0.31.1")
    freshness.bump(repo)
    text = (repo / ".github" / "workflows" / "deploy.yml").read_text()
    assert "k8s-deploy.yml@v0.31.1" in text and trigger_problems(text, "deploy.yml") == [], text


def test_bump_leaves_a_trigger_already_past_0_31(repo, monkeypatch):
    from pubkit import freshness  # noqa: PLC0415
    (repo / "pubkit.json").write_text('{"kind": "service"}\n')
    (repo / ".github" / "workflows").mkdir(parents=True)
    pinned = PUSHED.replace("@v0.30.0", "@v0.31.0")
    (repo / ".github" / "workflows" / "deploy.yml").write_text(pinned)
    monkeypatch.setattr(cli, "sync", lambda root, check: 0)
    monkeypatch.setattr(cli, "lock", lambda root: 0)
    monkeypatch.setattr(freshness, "__version__", "0.31.1")
    freshness.bump(repo)
    assert "push:" in (repo / ".github" / "workflows" / "deploy.yml").read_text()
