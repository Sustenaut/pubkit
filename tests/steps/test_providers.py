"""What `pubrules sync` and `pubrules bump` do for a deploy, now that the walk is
pubdeploy's: the managed with-secrets in every repository with a
with-secrets.json, the launchers' provider wheels, pubsec's and pubdeploy's
floors in deploy.providers, ./pubdeploy, and the sustenaut.sync and
sustenaut.bump hooks. In a scratch repository, with the hooks faked: pubrules
runs in this process (`I call pubrules with`), so the hooks it loads are these.

The checks are pubtest's; the scratch directory pubtest's file steps read is
the repository."""

from __future__ import annotations

import json
import shlex
import subprocess

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from pubrules import cli

scenarios("providers.feature")


@pytest.fixture
def repo(tmp_path, monkeypatch, pubtest_ctx):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setenv("PUBRULES_ANY_VERSION", "1")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    pubtest_ctx.directory = root
    return root


def init(root, kind, **extra):
    assert cli.main(["init", f"--kind={kind}", *(["--project=example"] if kind == "service" else [])]) == 0
    config = json.loads((root / "pubrules.json").read_text())
    (root / "pubrules.json").write_text(json.dumps(config | extra, indent=2))


def pin(name):
    """NAME's pin at the release the scenario's release state lists (conftest.RELEASES)."""
    from conftest import RELEASES  # noqa: PLC0415
    from pubrules.releases import wheel_url  # noqa: PLC0415
    return {"distribution": name, "wheel": wheel_url(name, RELEASES[name]["version"]),
            "sha256": RELEASES[name]["sha256"]}


def providers(text):
    """deploy.providers as a docstring gives it: {"release": NAME} is NAME's pin
    at the release the release state lists."""
    return [pin(p["release"]) if set(p) == {"release"} else p for p in json.loads(text)]


class Hook:
    """A stand-in for a package's hook, under the name `name`."""

    def __init__(self, name, does):
        self.name, self.value, self.does = name, f"{name}:hook", does

    def load(self):
        return self.does


@pytest.fixture
def hooks(monkeypatch):
    """The sustenaut.sync and sustenaut.bump hooks installed: pubdeploy's,
    faked, unless a step changes them."""
    installed = {"sustenaut.sync": [Hook("pubdeploy", lambda root, config, check: ([], False))],
                 "sustenaut.bump": []}
    monkeypatch.setattr("pubrules.hooks.entry_points", lambda group: installed[group])
    return installed


def deployed(repo, **deploy):
    init(repo, "service")
    config = json.loads((repo / "pubrules.json").read_text())
    (repo / "pubrules.json").write_text(json.dumps(config | {"deploy": {"namespace": "example"} | deploy}, indent=2) + "\n")


def exits(code):
    """A command's return, as the program's exit: what pubtest reads."""
    raise SystemExit(code)


# --- the repository -----------------------------------------------------------


@given(parsers.parse('a "{kind}" repository'))
def repository(repo, hooks, kind):
    init(repo, kind)


@given("a repository with a deploy block")
def with_deploy(repo, hooks):
    deployed(repo)


@given("a repository with a deploy block whose deploy.providers is:")
def with_providers(repo, hooks, docstring):
    deployed(repo, providers=providers(docstring))


@given("a repository with a deploy block that lists both providers at their releases")
def with_releases(repo, hooks):
    deployed(repo, providers=[pin("pubdeploy"), pin("pubsec")])


@given("pubrules.json gets a deploy block")
def gets_deploy(repo):
    config = json.loads((repo / "pubrules.json").read_text())
    (repo / "pubrules.json").write_text(json.dumps(config | {"deploy": {"namespace": "example"}}, indent=2) + "\n")


@given("pubrules has synced it")
def synced(repo, hooks):
    assert cli.main(["sync"]) == 0


# --- the hooks -----------------------------------------------------------------


@given(parsers.parse('pubdeploy\'s sync hook says "{line}"'))
def sync_hook_says(hooks, line):
    calls = []

    def check(root, config, check):
        calls.append((config["deploy"]["namespace"], check))
        return ([line], check)
    hooks["sustenaut.sync"] = [Hook("pubdeploy", check)]
    hooks["calls"] = calls


@given(parsers.parse('the sync hook "{name}" raises ValueError "{message}"'))
def sync_hook_raises(hooks, name, message):
    def broken(root, config, check):
        raise ValueError(message)
    hooks["sustenaut.sync"].append(Hook(name, broken))


@given("pubdeploy's hooks are not installed")
def no_pubdeploy(hooks):
    hooks["sustenaut.sync"] = []


@given(parsers.parse('the bump hook "{name}" raises OSError "{message}"'))
def bump_hook_raises(hooks, name, message):
    def broken(root):
        raise OSError(message)
    hooks["sustenaut.bump"].append(Hook(name, broken))


@given(parsers.parse('pubdeploy\'s bump hook moves its pin to "{wheel}" with the sha256 "{sha}"'))
def bump_hook_moves(hooks, wheel, sha):
    version = wheel.rsplit("/v", 1)[1].split("/", 1)[0]

    def moved(root):
        config = json.loads((root / "pubrules.json").read_text())
        config["deploy"]["providers"] = [pin("pubsec"), {"distribution": "pubdeploy", "sha256": sha, "wheel": wheel}]
        (root / "pubrules.json").write_text(json.dumps(config, indent=2) + "\n")
        return [f"updated  pubrules.json (deploy.providers pubdeploy: {version})"]
    hooks["sustenaut.bump"].append(Hook("pubdeploy", moved))


@given("pubdeploy's bump hook moves nothing")
def bump_hook_still(hooks):
    hooks["sustenaut.bump"] = [Hook("pubdeploy", lambda root: [])]


@pytest.fixture
def recorded():
    return {"lock": [], "sync": []}


@given(parsers.parse("{what} the repository is recorded, not done"))
def recorded_not_done(monkeypatch, recorded, what):
    verb = {"locking": "lock", "syncing": "sync"}[what]
    monkeypatch.setattr(f"pubrules.cli.{verb}", lambda root, *args, **kwargs: recorded[verb].append(root))


# --- the action ------------------------------------------------------------------


@when(parsers.parse('I call pubrules with "{command}"'), target_fixture="result")
def call(pubtest_ctx, repo, hooks, command):
    pubtest_ctx.attempt(lambda: exits(cli.main(shlex.split(command))))
    return pubtest_ctx.outcome


@when("I bump the repository", target_fixture="result")
def bumps(pubtest_ctx, repo, hooks):
    from pubrules.freshness import bump  # noqa: PLC0415
    pubtest_ctx.attempt(lambda: exits(bump(repo)))
    return pubtest_ctx.outcome


@when("the nightly moves the repository's pins", target_fixture="result")
def nightly_pins(pubtest_ctx, repo, hooks, monkeypatch):
    from pubrules import nightly  # noqa: PLC0415
    # Only the hooks move pins here: GitHub says no other release, so the
    # wheels the repository pins (pubtest's) stay whatever has been released.
    monkeypatch.setattr(nightly, "latest_release", lambda repo: None)
    pubtest_ctx.attempt(lambda: nightly.pins(repo, ""))
    return pubtest_ctx.outcome


@when("deploy.providers changes to:")
def providers_change(repo, docstring):
    config = json.loads((repo / "pubrules.json").read_text())
    config["deploy"]["providers"] = providers(docstring)
    (repo / "pubrules.json").write_text(json.dumps(config, indent=2) + "\n")


# --- what came of it ---------------------------------------------------------------


@then("pubrules.json is as it was but for deploy.providers, which is:")
def config_but_providers(pubtest_ctx, repo, docstring):
    """The entries, and nothing else of the file's layout: the text before
    the first action, with deploy.providers put first in the deploy block."""
    wanted = providers(docstring)
    before = pubtest_ctx.written["pubrules.json"].decode()
    after = (repo / "pubrules.json").read_text()
    assert json.loads(after)["deploy"]["providers"] == wanted, after
    assert after.replace(f'"providers": {json.dumps(wanted)},\n    ', "") == before, after


@then("the deploy.providers of pubrules.json is:")
def providers_are(repo, docstring):
    got = json.loads((repo / "pubrules.json").read_text())["deploy"]["providers"]
    assert got == providers(docstring), got


@then(parsers.parse('the file "{path}" ends with "{text}"'))
def ends_with(pubtest_ctx, path, text):
    from pubtest.steps.files import at  # noqa: PLC0415
    held = at(pubtest_ctx, path).read_text()
    assert held.rstrip().endswith(text), held


@then(parsers.parse('pubdeploy\'s sync hook was asked of the namespace "{namespace}" to write, then to check'))
def hook_asked(hooks, namespace):
    assert hooks["calls"] == [(namespace, False), (namespace, True)], hooks["calls"]


@then(parsers.parse("the repository was {verb} {count:d} times"))
def was_done(repo, recorded, verb, count):
    done = recorded[{"locked": "lock", "synced": "sync"}[verb]]
    assert done == [repo] * count, done
