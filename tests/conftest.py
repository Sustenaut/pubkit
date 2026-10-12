"""pubkit's own suite. It is also pubkit's first consumer: the steps it
does not define here (`it succeeds`, `it prints ...`) are the plugin's.

Steps and fixtures more than one steps module uses live here: the scratch
repository, running the `pubkit` command in it, and its files. pytest-bdd
registers a step only in the module that defines it, or in a conftest.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, then, when

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


@then(parsers.parse('"{path}" contains "{text}"'))
def contains(repo, path, text):
    assert text in (repo / path).read_text()


@given(parsers.re(r'pubkit (?P<args>(?:"[^"]*" ?)+) has run'))
def has_run(repo, run, args):
    argv = args.strip().strip('"').split('" "')
    assert run(pubkit(), *argv, cwd=repo).code == 0
