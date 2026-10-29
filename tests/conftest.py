"""pubkit's own suite. It is also pubkit's first consumer: the steps it
does not define here (`it succeeds`, `it prints ...`) are the plugin's.

Steps and fixtures more than one steps module uses live here: the scratch
repository, running the `pubkit` command in it, and its files. pytest-bdd
registers a step only in the module that defines it, or in a conftest.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, then, when

@pytest.fixture(autouse=True)
def offline_pubrel(tmp_path, monkeypatch):
    """No scenario reads pubrel's tags from GitHub: its repository is one
    that is not there, unless a scenario makes one (refs.py). A source tree
    has no release commit, so pubkit pins its own workflows by tag unless a
    scenario gives it one."""
    monkeypatch.setenv("PUBKIT_PUBREL_REMOTE", str(tmp_path / "no-pubrel"))
    monkeypatch.delenv("PUBKIT_RELEASE_COMMIT", raising=False)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    return root


def pubkit():
    """The `pubkit` installed beside the Python running this suite."""
    found = shutil.which("pubkit", path=str(Path(sys.executable).parent))
    assert found, "pubkit is not installed in this environment; run `make venv`"
    return found


@given("an empty repository")
def empty(repo):
    pass


@given(parsers.parse('a repository initialised as "{kind}"'))
def initialised(repo, run, kind):
    assert run(pubkit(), "init", f"--kind={kind}", cwd=repo).code == 0


@given(parsers.parse('the file "{path}" reads "{text}"'))
@when(parsers.parse('the file "{path}" reads "{text}"'))
def file_reads(repo, path, text):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text + "\n")


@pytest.fixture
def env():
    return {}


@when(parsers.re(r'I run pubkit (?P<args>(?:"[^"]*" ?)+)'), target_fixture="result")
def run_pubkit(repo, run, env, args):
    variables = {k: v for k, v in env.items() if k != "resolved"}
    return run(pubkit(), *args.strip().strip('"').split('" "'), cwd=repo, env=variables)


@then(parsers.parse("the repository has {paths}"))
def has(repo, paths):
    for path in paths.split(", "):
        assert (repo / path.strip('"')).is_file(), path


@given("PUBKIT_ANY_VERSION is set")
def any_version(env):
    env["PUBKIT_ANY_VERSION"] = "1"


@given(parsers.parse('pubkit is a release whose tag names "{commit}"'))
def released(env, commit):
    env["PUBKIT_RELEASE_COMMIT"] = commit


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
    env["PUBKIT_PUBREL_REMOTE"] = str(remote)


@when("pubrel's repository cannot be reached")
def pubrel_gone(tmp_path, env):
    env["PUBKIT_PUBREL_REMOTE"] = str(tmp_path / "gone")


@then(parsers.parse('"{path}" pins pubrel\'s release workflow to the commit of "{tag}"'))
def pins_pubrel(repo, pubrel_tags, path, tag):
    from pubkit.refs import OWNER  # noqa: PLC0415

    text = (repo / path).read_text()
    assert f"{OWNER}/pubrel/.github/workflows/release.yml@{pubrel_tags[tag]} # {tag}\n" in text, text


@then(parsers.parse('"{path}" pins pubkit\'s workflows to "{commit}"'))
def pins_pubkit(repo, path, commit):
    from pubkit import __version__  # noqa: PLC0415
    from pubkit.refs import OWNER  # noqa: PLC0415

    lines = [line for line in (repo / path).read_text().splitlines()
             if f"{OWNER}/pubkit/.github/workflows/" in line]
    assert lines, f"{path} calls no pubkit workflow"
    for line in lines:
        assert line.endswith(f".yml@{commit} # v{__version__}"), line


@given(parsers.parse('pubkit.json names the workflows owner "{owner}"'))
@when(parsers.parse('pubkit.json names the workflows owner "{owner}"'))
def workflows_owner(repo, owner):
    import json  # noqa: PLC0415

    path = repo / "pubkit.json"
    config = json.loads(path.read_text()) if path.exists() else {"kind": "utility"}
    config["workflows_owner"] = json.loads(owner) if owner.startswith("[") else owner
    path.write_text(json.dumps(config))


@then(parsers.parse('every pubkit and pubrel workflow "{path}" calls is "{owner}"\'s'))
def calls_owner(repo, path, owner):
    import re  # noqa: PLC0415

    calls = re.findall(r"uses: ([^/\s]+)/(pubkit|pubrel)/", (repo / path).read_text())
    assert calls, f"{path} calls no pubkit or pubrel workflow"
    assert {o for o, _ in calls} == {owner}, calls


# A pubkit action's pin, by what it is to the pubkit under test.
ACTION_PINS = {
    "this pubkit's commit": lambda v: "0f3574a72707a46bae149d3ed7a4644b4837795a # v" + v,
    "this pubkit's tag": lambda v: "v" + v,
    "another commit": lambda v: "1a49eb98c9647c1b60073d55b9ca0daf3eeee5c0 # v" + v,
    "another pubkit": lambda v: "1a49eb98c9647c1b60073d55b9ca0daf3eeee5c0 # v0.0.1",
    "main": lambda v: "main",
}


@given(parsers.parse('the workflow "{name}" uses "{owner}"\'s pubkit action "{action}" at "{pin}"'))
def uses_action(repo, name, owner, action, pin):
    from pubkit import __version__  # noqa: PLC0415

    path = repo / ".github" / "workflows" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("on: push\njobs:\n  deploy:\n    runs-on: ubuntu-latest\n    steps:\n"
                    f"      - uses: {owner}/pubkit/actions/{action}@{ACTION_PINS[pin](__version__)}\n"
                    "        with:\n          dir: public\n")


@then(parsers.parse('"{path}" uses "{owner}"\'s pubkit action "{action}" at "{pin}"'))
def action_at(repo, path, owner, action, pin):
    from pubkit import __version__  # noqa: PLC0415

    want = f"uses: {owner}/pubkit/actions/{action}@{ACTION_PINS[pin](__version__)}\n"
    assert want in (repo / path).read_text(), (repo / path).read_text()


@then(parsers.parse('"{path}" contains "{text}"'))
def contains(repo, path, text):
    assert text in (repo / path).read_text()


@then(parsers.parse('"{path}" does not contain "{text}"'))
def lacks(repo, path, text):
    assert text not in (repo / path).read_text()


@given(parsers.re(r'pubkit (?P<args>(?:"[^"]*" ?)+) has run'))
def has_run(repo, run, args):
    argv = args.strip().strip('"').split('" "')
    assert run(pubkit(), *argv, cwd=repo).code == 0
