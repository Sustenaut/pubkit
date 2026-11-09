"""What `pubkit sync` and `pubkit bump` do for a deploy, now that the walk is
pubdeploy's: the managed with-secrets in every repository with a
with-secrets.json, the launchers' provider wheels, pubsec's and pubdeploy's
floors in deploy.providers, ./pubdeploy, and the sustenaut.sync and
sustenaut.bump hooks. In a scratch repository, with the hooks faked."""

from __future__ import annotations

import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from pubkit import cli

OLD_SCRIPT = "#!/usr/bin/env bash\nexec bws run -- \"$@\"\n"
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
            "project it read (PUBKIT_SECRETS_PROJECT), so pubdeploy finds every bitwarden.* need unknown "
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


# --- pubsec: deploy.providers, ./pubsec and the new with-secrets ----------------


def pin(name="pubsec"):
    from pubkit.new import FLOORS  # noqa: PLC0415
    version, sha, wheel = FLOORS[name]
    return {"distribution": name, "wheel": wheel.format(version), "sha256": sha}


def both():
    """deploy.providers as sync lists it from nothing: each entry goes first."""
    return [pin("pubdeploy"), pin()]


class Hook:
    """A stand-in for a package's hook, under the name `name`."""

    def __init__(self, name, does):
        self.name, self.value, self.does = name, f"{name}:hook", does

    def load(self):
        return self.does


@pytest.fixture
def hooks(monkeypatch):
    """The sustenaut.sync and sustenaut.bump hooks installed: pubdeploy's,
    faked, unless a test changes them."""
    installed = {"sustenaut.sync": [Hook("pubdeploy", lambda root, config, check: ([], False))],
                 "sustenaut.bump": []}
    monkeypatch.setattr("pubkit.hooks.entry_points", lambda group: installed[group])
    return installed


def deployed(repo, hooks, **deploy):
    init(repo, "service")
    config = json.loads((repo / "pubkit.json").read_text())
    (repo / "pubkit.json").write_text(json.dumps(config | {"deploy": {"namespace": "example"} | deploy}, indent=2) + "\n")


def test_sync_lists_pubsec_and_pubdeploy_in_deploy_providers_and_check_fails_without_them(repo, capsys, hooks):
    deployed(repo, hooks)
    before = (repo / "pubkit.json").read_text()
    assert cli.main(["sync", "--check"]) == 1
    err = capsys.readouterr().err
    assert "pubkit: deploy.providers does not list pubsec: the types that hold a secret's value are pubsec's" in err
    assert "pubkit: deploy.providers does not list pubdeploy: the deploy walk, and the check sync runs of the " \
        "block, are pubdeploy's" in err
    assert (repo / "pubkit.json").read_text() == before
    assert cli.main(["sync"]) == 0
    after = (repo / "pubkit.json").read_text()
    assert json.loads(after)["deploy"]["providers"] == both()
    # The entries, and nothing else of the file's layout.
    assert after.replace(f'"providers": [{json.dumps(pin("pubdeploy"))}, {json.dumps(pin())}],\n    ', "") == before
    assert cli.main(["sync", "--check"]) == 0, capsys.readouterr().err


def test_sync_writes_pubsec_and_with_secrets_runs_it(repo, capsys, hooks):
    deployed(repo, hooks)
    assert cli.main(["sync"]) == 0
    launcher, wrapper = (repo / "pubsec").read_text(), (repo / "with-secrets").read_text()
    assert f"fetch {pin()['wheel']} {pin()['sha256']} providers deploy.providers\n" in launcher
    assert 'exec uvx --from "$fetched" pubsec "$@"' in launcher
    assert (repo / "pubsec").stat().st_mode & 0o111
    assert wrapper.rstrip().endswith('exec "$(cd "$(dirname "$0")" && pwd)/pubsec" run --cwd "$PWD" -- "$@"')
    # ./pubkit runs with both, so pubdeploy's hooks and pubsec's types load.
    for name in ("pubsec", "pubdeploy"):
        assert f"provider {pin(name)['wheel']} {pin(name)['sha256']}\n" in (repo / "pubkit").read_text()


def test_sync_writes_pubdeploy_which_runs_with_the_other_providers(repo, capsys, hooks, tmp_path):
    deployed(repo, hooks)
    assert cli.main(["sync"]) == 0
    text = (repo / "pubdeploy").read_text()
    assert (repo / "pubdeploy").stat().st_mode & 0o111
    assert f"fetch {pin('pubdeploy')['wheel']} {pin('pubdeploy')['sha256']} providers deploy.providers\n" in text
    assert f"provider {pin()['wheel']} {pin()['sha256']}\n" in text
    assert f"provider {pin('pubdeploy')['wheel']}" not in text
    assert text.rstrip().endswith('exec uvx --from "$fetched" ${with[@]+"${with[@]}"} pubdeploy "$@"')
    # The wheels as the launcher finds them in the cache: pubdeploy's is the one uvx runs.
    cache = tmp_path / "cache"
    for name in ("pubsec", "pubdeploy"):
        held = cache / "pubkit" / "providers" / f"{name}-sha" / pin(name)["wheel"].rsplit("/", 1)[1]
    part = text[text.index("# A wheel"):text.index("exec uvx")]
    script = part.replace(pin()["sha256"], "s" * 64).replace(pin("pubdeploy")["sha256"], "d" * 64)
    for name, sha in (("pubsec", "s" * 64), ("pubdeploy", "d" * 64)):
        held = cache / "pubkit" / "providers" / sha / pin(name)["wheel"].rsplit("/", 1)[1]
        held.parent.mkdir(parents=True)
        held.write_bytes(name.encode())
    sums = {hashlib.sha256(n.encode()).hexdigest(): s for n, s in (("pubsec", "s" * 64), ("pubdeploy", "d" * 64))}
    for real, fake in sums.items():
        script = script.replace(fake, real)
        (cache / "pubkit" / "providers" / fake).rename(cache / "pubkit" / "providers" / real)
    done = subprocess.run(["bash", "-c", f"set -euo pipefail\n{script}printf '%s\\n' \"$fetched\" \"${{with[@]}}\"\n"],
                          env={"PATH": "/usr/bin:/bin", "XDG_CACHE_HOME": str(cache)}, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    fetched, *rest = done.stdout.split()
    assert fetched.endswith(pin("pubdeploy")["wheel"].rsplit("/", 1)[1])
    assert rest[0] == "--with" and rest[1].endswith(pin()["wheel"].rsplit("/", 1)[1]) and len(rest) == 2


def test_without_a_deploy_block_there_is_no_pubdeploy(repo, capsys, hooks):
    init(repo, "service")
    assert cli.main(["sync"]) == 0
    assert not (repo / "pubdeploy").exists()


def test_a_floored_package_below_the_floor_is_moved_up_and_a_newer_one_kept(repo, capsys, hooks):
    old = {"distribution": "pubsec", "sha256": "0" * 64,
           "wheel": "https://github.com/example-org/pubsec/releases/download/v0.0.1/pubsec-0.0.1-py3-none-any.whl"}
    deployed(repo, hooks, providers=[old, pin("pubdeploy")])
    assert cli.main(["sync", "--check"]) == 1
    assert "lists a pubsec older than this pubkit works with" in capsys.readouterr().err
    assert cli.main(["sync"]) == 0
    assert json.loads((repo / "pubkit.json").read_text())["deploy"]["providers"] == [pin(), pin("pubdeploy")]
    newer = {"distribution": "pubdeploy", "sha256": "1" * 64,
             "wheel": "https://github.com/example-org/pubdeploy/releases/download/v9.0.0/pubdeploy-9.0.0-py3-none-any.whl"}
    config = json.loads((repo / "pubkit.json").read_text())
    config["deploy"]["providers"] = [pin(), newer]
    (repo / "pubkit.json").write_text(json.dumps(config, indent=2) + "\n")
    assert cli.main(["sync"]) == 0
    assert json.loads((repo / "pubkit.json").read_text())["deploy"]["providers"] == [pin(), newer]
    assert f"fetch {newer['wheel']} {newer['sha256']} providers deploy.providers\n" in (repo / "pubdeploy").read_text()
    assert cli.main(["sync", "--check"]) == 0, capsys.readouterr().err


# --- the hooks ------------------------------------------------------------------


def test_the_sync_hooks_run_and_their_lines_are_printed(repo, capsys, hooks):
    calls = []

    def check(root, config, check):
        calls.append((root, config["deploy"]["namespace"], check))
        return (["pubdeploy: deploy: drift, k8s.secrets-declared:example"], check)
    hooks["sustenaut.sync"] = [Hook("pubdeploy", check)]
    deployed(repo, hooks, providers=both())
    assert cli.main(["sync"]) == 0
    assert "pubdeploy: deploy: drift, k8s.secrets-declared:example" in capsys.readouterr().err
    assert cli.main(["sync", "--check"]) == 1
    assert "pubdeploy: deploy: drift" in capsys.readouterr().err
    assert [(c[1], c[2]) for c in calls] == [("example", False), ("example", True)]


def test_a_sync_hook_that_fails_is_said_by_its_type_alone_and_fails_check(repo, capsys, hooks):
    secret = "canary-7f3e"

    def broken(root, config, check):
        raise ValueError(f"the token is {secret}")
    hooks["sustenaut.sync"] = [Hook("pubdeploy", lambda root, config, check: ([], False)), Hook("other", broken)]
    deployed(repo, hooks, providers=both())
    assert cli.main(["sync"]) == 0
    assert cli.main(["sync", "--check"]) == 1
    err = capsys.readouterr().err
    assert "pubkit: the sustenaut.sync hook other failed (ValueError)" in err and secret not in err


def test_a_deploy_block_without_pubdeploy_installed_fails_check(repo, capsys, hooks):
    hooks["sustenaut.sync"] = []
    deployed(repo, hooks, providers=both())
    assert cli.main(["sync"]) == 0
    assert cli.main(["sync", "--check"]) == 1
    assert "pubkit: deploy: the deploy block is pubdeploy's to check, and pubdeploy is not installed" \
        in capsys.readouterr().err


def test_a_repository_without_a_deploy_block_needs_no_pubdeploy(repo, capsys, hooks):
    hooks["sustenaut.sync"] = []
    init(repo, "utility")
    assert cli.main(["sync"]) == 0
    assert cli.main(["sync", "--check"]) == 0, capsys.readouterr().err


def test_bump_runs_the_bump_hooks_before_sync_writes_the_launchers(repo, capsys, hooks, monkeypatch):
    deployed(repo, hooks, providers=both())
    newer = {"distribution": "pubdeploy", "sha256": "2" * 64,
             "wheel": "https://github.com/example-org/pubdeploy/releases/download/v9.1.0/pubdeploy-9.1.0-py3-none-any.whl"}

    def moved(root):
        config = json.loads((root / "pubkit.json").read_text())
        config["deploy"]["providers"] = [pin(), newer]
        (root / "pubkit.json").write_text(json.dumps(config, indent=2) + "\n")
        return ["updated  pubkit.json (deploy.providers pubdeploy: 9.1.0)"]

    def broken(root):
        raise OSError("no network")
    hooks["sustenaut.bump"] = [Hook("other", broken), Hook("pubdeploy", moved)]
    monkeypatch.setattr("pubkit.cli.lock", lambda root: None)
    from pubkit.freshness import bump  # noqa: PLC0415
    assert bump(repo) == 0
    out = capsys.readouterr().out
    assert "pubkit: the sustenaut.bump hook other failed (OSError)" in out
    assert "updated  pubkit.json (deploy.providers pubdeploy: 9.1.0)" in out
    assert f"fetch {newer['wheel']} {newer['sha256']} providers deploy.providers\n" in (repo / "pubdeploy").read_text()


def test_the_removed_commands_are_gone():
    for command in (["deploy", "walk"], ["k8s", "preflight"]):
        with pytest.raises(SystemExit) as stopped:
            cli.main(command)
        assert stopped.value.code == 2
