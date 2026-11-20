"""pubrules' own suite. It is also pubrules' first consumer: the steps it
does not define here (`it succeeds`, `it prints ...`) are the plugin's.

Steps and fixtures more than one steps module uses live here: the scratch
repository, running the `pubrules` command in it, and its files. pytest-bdd
registers a step only in the module that defines it, or in a conftest.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, then, when

# This checkout: the workflows and actions pubrules ships are its own.
SOURCE = Path(__file__).resolve().parents[1]

@pytest.fixture(scope="session", autouse=True)
def rust_toolchain(tmp_path_factory):
    """The Rust toolchain pubrules' rust-toolchain.toml pins, installed once
    before any test runs cargo. rustup installs it on first use, but under
    pytest-xdist several workers' first cargo runs at once, and rustup's
    concurrent installs of one toolchain fail; each worker takes a lock in
    the run's shared base directory, and only the first installs."""
    rustup = shutil.which("rustup")
    if rustup is None:
        return
    import fcntl  # noqa: PLC0415
    import tomllib  # noqa: PLC0415
    from importlib import resources  # noqa: PLC0415
    pinned = tomllib.loads((resources.files("pubrules") / "templates" / "rust" / "rust-toolchain.toml")
                           .read_text(encoding="utf-8"))["toolchain"]
    shared = tmp_path_factory.getbasetemp().parent
    with open(shared / "rust-toolchain.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        done = shared / f"rust-toolchain-{pinned['channel']}"
        if not done.exists():
            subprocess.run([rustup, "toolchain", "install", pinned["channel"], "--profile",
                            pinned.get("profile", "minimal"),
                            *(f"--component={c}" for c in pinned.get("components", []))],
                           check=True, capture_output=True)
            done.touch()


@pytest.fixture(autouse=True)
def offline_pubrel(tmp_path, monkeypatch):
    """No scenario reads pubrel's tags from GitHub: its repository is one
    that is not there, unless a scenario makes one (refs.py). A source tree
    has no release commit, so pubrules pins its own workflows by tag unless a
    scenario gives it one."""
    monkeypatch.setenv("PUBRULES_PUBREL_REMOTE", str(tmp_path / "no-pubrel"))
    monkeypatch.delenv("PUBRULES_RELEASE_COMMIT", raising=False)


# The releases a pin pubrules writes where a repository has none comes from
# (releases.py): a release-state file, so no scenario reads GitHub's.
# Each wheel is named too, so no scenario run as a program asks GitHub.
RELEASES = {"pubrel": "0.7.0",
            "pubtest": {"version": "0.2.2", "wheel": "pubtest-0.2.2-py3-none-any.whl"},
            "pubsec": {"version": "0.3.0", "wheel": "pubsec-0.3.0-py3-none-any.whl",
                       "sha256": "a9036c741e29098cdd04baf64c7873e0ff1dd6e7bde12e9671a3940370724e18"},
            "pubdeploy": {"version": "0.5.0", "wheel": "pubdeploy-0.5.0-py3-none-any.whl",
                          "sha256": "320d68e081c674457294e6ed3a6c8ab9e2606ca4a2946c252a34021b62dc70cd"}}


@pytest.fixture(autouse=True)
def no_release_assets(monkeypatch):
    """No scenario asks GitHub what a release's files are called: a release
    says nothing, so its wheel is named by its repository, unless a scenario
    says otherwise."""
    from pubrules import nightly  # noqa: PLC0415
    monkeypatch.setattr(nightly, "release_assets", lambda repo, tag: None)
    nightly.release_wheel.cache_clear()


@pytest.fixture(autouse=True)
def release_state(tmp_path, monkeypatch):
    """The release-state file every scenario's pubrules reads, with RELEASES."""
    path = tmp_path / "release-state.json"
    path.write_text(json.dumps({"releases": RELEASES}))
    monkeypatch.setenv("PUBRULES_RELEASES", str(path))
    return path


@given(parsers.parse('the release state lists "{name}" at "{version}"'))
def release_state_lists(release_state, name, version):
    data = json.loads(release_state.read_text())
    data["releases"][name] = version if name == "pubrel" else \
        {"version": version, "wheel": f"{name}-{version}-py3-none-any.whl"}
    release_state.write_text(json.dumps(data))


@given(parsers.parse('the release state lists no "{name}"'))
def release_state_lacks(release_state, name):
    data = json.loads(release_state.read_text())
    del data["releases"][name]
    release_state.write_text(json.dumps(data))


# A scenario and its binding, so `sync --check` gets past the Gherkin rule
# (gherkin.py) in a scratch repository a scenario builds by hand. At the
# root, out of the way of the tests/ a scenario makes.
SEED = {"seed.feature": "Feature: Seed\n  Scenario: Seed\n    When it is read\n    Then it holds\n",
        "test_seed.py": 'from pytest_bdd import scenarios\n\nscenarios("seed.feature")\n'}


@pytest.fixture
def repo(tmp_path, pubtest_ctx):
    """The scratch repository, which pubtest's file steps read too."""
    root = tmp_path / "repo"
    root.mkdir()
    for path, text in SEED.items():
        (root / path).write_text(text)
    pubtest_ctx.directory = root
    return root


def pubrules():
    """The `pubrules` installed beside the Python running this suite."""
    found = shutil.which("pubrules", path=str(Path(sys.executable).parent))
    assert found, "pubrules is not installed in this environment; run `make venv`"
    return found


@given("an empty repository")
def empty(repo):
    pass


@given(parsers.parse('a repository initialised as "{kind}"'))
def initialised(repo, run, kind):
    assert run(pubrules(), "init", f"--kind={kind}", cwd=repo).code == 0


@pytest.fixture
def env():
    return {}


@when(parsers.re(r'I run pubrules (?P<args>(?:"[^"]*" ?)+)'), target_fixture="result")
def run_pubrules(repo, run, env, args):
    variables = {k: v for k, v in env.items() if k != "resolved"}
    return run(pubrules(), *args.strip().strip('"').split('" "'), cwd=repo, env=variables)


@then(parsers.parse("the repository has {paths}"))
def has(repo, paths):
    for path in paths.split(", "):
        assert (repo / path.strip('"')).is_file(), path


@given("PUBRULES_ANY_VERSION is set")
def any_version(env):
    env["PUBRULES_ANY_VERSION"] = "1"


@given(parsers.parse('pubrules is a release whose tag names "{commit}"'))
def released(env, commit):
    env["PUBRULES_RELEASE_COMMIT"] = commit


@pytest.fixture
def pubrel_tags():
    return {}


@given(parsers.parse('pubrel\'s repository tags "{tag}"'))
def pubrel_tagged(tmp_path, env, pubrel_tags, tag):
    """A stand-in for pubrel's repository, with an annotated tag, as
    pubrel's are: the pin is to the commit it names, not to the tag."""
    remote = tmp_path / "pubrel"
    if not remote.exists():
        remote.mkdir()
        subprocess.run(["git", "init", "-q", str(remote)], check=True)
    who = ["-c", "user.name=t", "-c", "user.email=t@example.com"]
    git = ["git", "-C", str(remote), *who]
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", tag], check=True)
    subprocess.run([*git, "tag", "-a", tag, "-m", tag], check=True)
    pubrel_tags[tag] = subprocess.run([*git, "rev-parse", "HEAD"], check=True,
                                      capture_output=True, text=True).stdout.strip()
    env["PUBRULES_PUBREL_REMOTE"] = str(remote)


@when("pubrel's repository cannot be reached")
def pubrel_gone(tmp_path, env):
    env["PUBRULES_PUBREL_REMOTE"] = str(tmp_path / "gone")


@then(parsers.parse('"{path}" pins pubrel\'s release workflow to the commit of "{tag}"'))
def pins_pubrel(repo, pubrel_tags, path, tag):
    from pubrules.refs import OWNER  # noqa: PLC0415

    text = (repo / path).read_text()
    assert f"{OWNER}/pubrel/.github/workflows/release.yml@{pubrel_tags[tag]} # {tag}\n" in text, text


@then(parsers.parse('"{path}" pins pubrules\' workflows to "{commit}"'))
def pins_pubrules(repo, path, commit):
    from pubrules import __version__  # noqa: PLC0415
    from pubrules.refs import OWNER  # noqa: PLC0415

    lines = [line for line in (repo / path).read_text().splitlines()
             if f"{OWNER}/pubrules/.github/workflows/" in line]
    assert lines, f"{path} calls no pubrules workflow"
    for line in lines:
        assert line.endswith(f".yml@{commit} # v{__version__}"), line


@given(parsers.parse('pubrules.json names the workflows owner "{owner}"'))
@when(parsers.parse('pubrules.json names the workflows owner "{owner}"'))
def workflows_owner(repo, owner):
    import json  # noqa: PLC0415

    path = repo / "pubrules.json"
    config = json.loads(path.read_text()) if path.exists() else {"kind": "utility"}
    config["workflows_owner"] = json.loads(owner) if owner.startswith("[") else owner
    path.write_text(json.dumps(config))


@then(parsers.parse('every pubrules and pubrel workflow "{path}" calls is "{owner}"\'s'))
def calls_owner(repo, path, owner):
    import re  # noqa: PLC0415

    calls = re.findall(r"uses: ([^/\s]+)/(pubrules|pubrel)/", (repo / path).read_text())
    assert calls, f"{path} calls no pubrules or pubrel workflow"
    assert {o for o, _ in calls} == {owner}, calls


# A pubrules action's pin, by what it is to the pubrules under test.
ACTION_PINS = {
    "this pubrules' commit": lambda v: "0f3574a72707a46bae149d3ed7a4644b4837795a # v" + v,
    "this pubrules' tag": lambda v: "v" + v,
    "another commit": lambda v: "1a49eb98c9647c1b60073d55b9ca0daf3eeee5c0 # v" + v,
    "another pubrules": lambda v: "1a49eb98c9647c1b60073d55b9ca0daf3eeee5c0 # v0.0.1",
    "main": lambda v: "main",
}


@given(parsers.parse('the workflow "{name}" uses "{owner}"\'s pubrules action "{action}" at "{pin}"'))
def uses_action(repo, name, owner, action, pin):
    from pubrules import __version__  # noqa: PLC0415

    path = repo / ".github" / "workflows" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("on: push\njobs:\n  deploy:\n    runs-on: ubuntu-latest\n    steps:\n"
                    f"      - uses: {owner}/pubrules/actions/{action}@{ACTION_PINS[pin](__version__)}\n"
                    "        with:\n          dir: public\n")


@given(parsers.parse('the workflow "{name}" calls "{owner}"\'s pubrules workflow "{workflow}" at "{pin}"'))
def calls_workflow(repo, name, owner, workflow, pin):
    from pubrules import __version__  # noqa: PLC0415

    path = repo / ".github" / "workflows" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("on: pull_request\njobs:\n  ci:\n"
                    f"    uses: {owner}/pubrules/.github/workflows/{workflow}@{ACTION_PINS[pin](__version__)}\n")


@then(parsers.parse('"{path}" calls "{owner}"\'s pubrules workflow "{workflow}" at "{pin}"'))
def workflow_at(repo, path, owner, workflow, pin):
    from pubrules import __version__  # noqa: PLC0415

    want = f"uses: {owner}/pubrules/.github/workflows/{workflow}@{ACTION_PINS[pin](__version__)}\n"
    assert want in (repo / path).read_text(), (repo / path).read_text()


@then(parsers.parse('"{path}" uses "{owner}"\'s pubrules action "{action}" at "{pin}"'))
def action_at(repo, path, owner, action, pin):
    from pubrules import __version__  # noqa: PLC0415

    want = f"uses: {owner}/pubrules/actions/{action}@{ACTION_PINS[pin](__version__)}\n"
    assert want in (repo / path).read_text(), (repo / path).read_text()


@given(parsers.re(r'pubrules (?P<args>(?:"[^"]*" ?)+) has run'))
def has_run(repo, run, args):
    argv = args.strip().strip('"').split('" "')
    assert run(pubrules(), *argv, cwd=repo).code == 0


@when("I read the workflows and actions pubrules ships", target_fixture="shipped")
def read_shipped(pubtest_ctx):
    """Each reusable workflow and action, by name, as text."""
    def read():
        return {"workflows": {p.name: p.read_text() for p in sorted((SOURCE / ".github" / "workflows").glob("*.yml"))},
                "actions": {p.parent.name: p.read_text() for p in sorted((SOURCE / "actions").glob("*/action.yml"))}}
    return pubtest_ctx.attempt(read)


def render_launcher(pubtest_ctx, config):
    """./pubrules, as sync writes it from this pubrules.json, in the scratch directory."""
    import json  # noqa: PLC0415

    from pubrules import cli  # noqa: PLC0415
    from pubtest.steps.files import at  # noqa: PLC0415

    text = cli.render(cli.templates("utility")["pubrules"], cli.values(json.loads(config)))
    at(pubtest_ctx, "pubrules").write_text(text)


@given(parsers.parse("pubrules renders the utility launcher for '{config}'"))
def rendered(pubtest_ctx, config):
    render_launcher(pubtest_ctx, config)


@when(parsers.parse("pubrules renders the utility launcher for '{config}'"))
def renders(pubtest_ctx, config):
    pubtest_ctx.attempt(lambda: render_launcher(pubtest_ctx, config))
