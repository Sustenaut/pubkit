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
    part = text[text.index("# A wheel"):text.index("exec uvx")]
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
    assert rewritten.count("workflow_dispatch") == 1 and "  actions: read\n  contents: read\n" in rewritten


def test_the_trigger_rewrite_adds_workflow_dispatch():
    only = PUSHED.replace("  workflow_dispatch: {}\n", "").replace("@v0.30.0", "@v0.31.1")
    rewritten = retrigger(only)
    assert "  workflow_dispatch: {}\n" in rewritten and trigger_problems(rewritten, "deploy.yml") == [], rewritten


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


def test_bump_leaves_a_provider_without_a_sha256_saying_so(repo):
    (repo / "pubkit.json").write_text(json.dumps({"kind": "service", "deploy": {"namespace": "example", "providers": [
        {"distribution": "example-needs", "wheel": WHEEL}]}}, indent=2))
    before = (repo / "pubkit.json").read_text()
    lines = providers.bump(repo, lambda *_: (_ for _ in ()).throw(AssertionError("no release is read")))
    assert lines == ["left     deploy.providers example-needs: it has no sha256 to move; give it the wheel's, "
                     "as `pubkit sync --check` says"]
    assert (repo / "pubkit.json").read_text() == before


# --- pubsec: deploy.providers, ./pubsec and the new with-secrets ----------------


def pin():
    from pubkit.new import PUBSEC_FLOOR, PUBSEC_WHEEL  # noqa: PLC0415
    return {"distribution": "pubsec", "wheel": PUBSEC_WHEEL.format(PUBSEC_FLOOR[0]), "sha256": PUBSEC_FLOOR[1]}


def deployed(repo, monkeypatch, **deploy):
    init(repo, "service")
    config = json.loads((repo / "pubkit.json").read_text())
    (repo / "pubkit.json").write_text(json.dumps(config | {"deploy": deploy_repo(repo) | deploy}, indent=2) + "\n")
    monkeypatch.setattr("pubkit.deploy.offline_lines", lambda root, config: ([], False))


def test_sync_lists_pubsec_in_deploy_providers_and_check_fails_without_it(repo, capsys, monkeypatch):
    deployed(repo, monkeypatch)
    before = (repo / "pubkit.json").read_text()
    assert cli.main(["sync", "--check"]) == 1
    assert "pubkit: deploy.providers does not list pubsec: the types that hold a secret's value are pubsec's" \
        in capsys.readouterr().err
    assert (repo / "pubkit.json").read_text() == before
    assert cli.main(["sync"]) == 0
    after = (repo / "pubkit.json").read_text()
    assert json.loads(after)["deploy"]["providers"] == [pin()]
    # The entry, and nothing else of the file's layout.
    assert after.replace(f'"providers": [{json.dumps(pin())}],\n    ', "") == before
    assert cli.main(["sync", "--check"]) == 0, capsys.readouterr().err


def test_sync_writes_pubsec_and_with_secrets_runs_it(repo, capsys, monkeypatch):
    deployed(repo, monkeypatch)
    assert cli.main(["sync"]) == 0
    launcher, wrapper = (repo / "pubsec").read_text(), (repo / "with-secrets").read_text()
    assert f"fetch {pin()['wheel']} {pin()['sha256']} providers deploy.providers\n" in launcher
    assert 'exec uvx --from "$fetched" pubsec "$@"' in launcher
    assert (repo / "pubsec").stat().st_mode & 0o111
    assert wrapper.rstrip().endswith('exec "$(cd "$(dirname "$0")" && pwd)/pubsec" run --cwd "$PWD" -- "$@"')
    assert "secrets run" not in wrapper
    # ./pubkit runs the walk with pubsec, so its types load.
    assert f"provider {pin()['wheel']} {pin()['sha256']}\n" in (repo / "pubkit").read_text()


def test_a_pubsec_below_the_floor_is_moved_up_and_a_newer_one_kept(repo, capsys, monkeypatch):
    old = {"distribution": "pubsec", "sha256": "0" * 64,
           "wheel": "https://github.com/example-org/pubsec/releases/download/v0.0.1/pubsec-0.0.1-py3-none-any.whl"}
    deployed(repo, monkeypatch, providers=[old])
    assert cli.main(["sync", "--check"]) == 1
    assert "lists a pubsec older than this pubkit works with" in capsys.readouterr().err
    assert cli.main(["sync"]) == 0
    assert json.loads((repo / "pubkit.json").read_text())["deploy"]["providers"] == [pin()]
    newer = {"distribution": "pubsec", "sha256": "1" * 64,
             "wheel": "https://github.com/example-org/pubsec/releases/download/v9.0.0/pubsec-9.0.0-py3-none-any.whl"}
    config = json.loads((repo / "pubkit.json").read_text())
    config["deploy"]["providers"] = [newer]
    (repo / "pubkit.json").write_text(json.dumps(config, indent=2) + "\n")
    assert cli.main(["sync"]) == 0
    assert json.loads((repo / "pubkit.json").read_text())["deploy"]["providers"] == [newer]
    assert f"fetch {newer['wheel']} {newer['sha256']} providers deploy.providers\n" in (repo / "pubsec").read_text()
    assert cli.main(["sync", "--check"]) == 0, capsys.readouterr().err


def test_without_pubsec_its_types_name_it():
    from pubkit.deploy.model import Need  # noqa: PLC0415
    from pubkit.deploy.registry import BUILTIN, lacking  # noqa: PLC0415
    from pubkit.deploy.schema import need_problems  # noqa: PLC0415
    own = {kind.type: kind for kind in BUILTIN}
    assert need_problems({"type": "bitwarden.secret", "name": "x"}, 0, own, FIXTURE) == [
        "deploy: needs[0]: bitwarden.secret is pubsec's: list pubsec in deploy.providers, as `pubkit sync` does, "
        "and run ./pubkit, which runs it"]
    assert lacking([Need("k8s.secret", "x", {}), Need("github.secret", "Y", {})], own) == [
        "deploy: github.secret, k8s.secret are pubsec's, which is not installed: list pubsec in deploy.providers, "
        "as `pubkit sync` does, and run ./pubkit, which runs it"]


# The Context's names a provider may use, which pubkit's README lists under
# "Provider contract": changing one breaks pubsec and pg-backup.
CONTRACT = {"root", "config", "block", "namespace", "unit", "channel", "kubectl", "server", "gh", "send",
            "bitwarden_writer", "http_get", "env", "now", "rendered", "offline", "slug", "zones", "resolve",
            "values", "types", "needs", "results", "sleep", "dispatched"}


def test_the_provider_contract_is_on_the_context():
    from dataclasses import fields  # noqa: PLC0415

    from pubkit.deploy.model import Context  # noqa: PLC0415
    assert CONTRACT <= {f.name for f in fields(Context)}


def test_the_readme_lists_the_provider_contract():
    from pathlib import Path  # noqa: PLC0415
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text()
    section = readme.split("### Provider contract", 1)[1].split("\n## ", 1)[0]
    import re  # noqa: PLC0415
    missing = [name for name in CONTRACT if not re.search(rf"`{name}[`(]", section)]
    assert not missing, missing


def test_a_walk_hook_that_fails_is_said_and_the_walk_goes_on(capsys):
    from types import SimpleNamespace  # noqa: PLC0415

    from pubkit.deploy import prepare  # noqa: PLC0415
    from pubkit.deploy.model import Context  # noqa: PLC0415
    secret = "canary-7f3e"

    def broken(ctx, plan, terminal):
        raise ValueError(f"the token is {secret}")

    def says(ctx, plan, terminal):
        ctx.bitwarden_writer = "a writer"
        return "a line"
    points = [SimpleNamespace(name="broken", load=lambda: broken), SimpleNamespace(name="says", load=lambda: says)]
    ctx = Context.for_tests()
    prepare(ctx, [], True, points)
    err = capsys.readouterr().err
    assert "pubkit: deploy: the sustenaut.walk hook broken failed (ValueError)" in err and "a line" in err
    assert secret not in err and ctx.bitwarden_writer == "a writer"
