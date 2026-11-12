"""Steps for pubkit-python.mk, the make targets every Python package shares."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from pytest_bdd import given, parsers, scenarios, then

from pubkit import cli

scenarios("python.feature")


@given(parsers.parse("a {kind} package"))
def package(repo, kind):
    config = {"kind": {"Python": "utility", "Node": "utility", "Rust": "rust"}[kind]}
    if kind == "Node":
        (repo / "package.json").write_text("{}\n")
    (repo / "pubkit.json").write_text(json.dumps(config))


@given(parsers.parse("its pyproject.toml's [tool.coverage.run] is '{run}'"))
def coverage_run(repo, run):
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n\n[tool.coverage.run]\n'
        + run.replace("\\n", "\n") + "\n")


@then("the Makefile includes pubkit-python.mk")
def includes(repo):
    assert cli.INCLUDES_PYTHON_MK.search((repo / "Makefile").read_text())


@then(parsers.parse('the repository has no "{path}"'))
def has_no(repo, path):
    assert not (repo / path).exists()


def _make(repo, words):
    """`make -n` of the words: what it would run, without running it."""
    return subprocess.run(["make", "-n", "--no-print-directory", *words.split()[1:]], cwd=repo,
                          capture_output=True, text=True, check=True, env={**os.environ, "MAKEFLAGS": ""}).stdout


@then(parsers.parse('"{words}" runs pytest with "{args}" and writes coverage/coverage.xml'))
def runs_with(repo, words, args):
    out = _make(repo, words)
    assert re.search(r"coverage run -m pytest -q " + re.escape(args) + r"\s*$", out, re.M), out
    assert "coverage combine -q" in out and "coverage xml -q" in out, out


@then(parsers.parse('"{words}" runs pytest without "{flag}"'))
def runs_without(repo, words, flag):
    out = _make(repo, words)
    line = next(l for l in out.splitlines() if "coverage run -m pytest" in l)
    assert flag not in line.split("pytest", 1)[1], line


@then(parsers.parse('"{words}" runs the suite with "{env}"'))
def runs_env(repo, words, env):
    out = _make(repo, words)
    assert re.search(r"cd tests && " + re.escape(env) + r" COVERAGE_RCFILE=", out), out


@then(parsers.parse('"{words}" lists "{targets}"'))
def lists(repo, words, targets):
    out = subprocess.run(["make", "--no-print-directory", *words.split()[1:]], cwd=repo,
                         capture_output=True, text=True, check=True).stdout
    listed = re.findall(r"^\s+([a-z-]+)\s", out, re.M)
    assert set(targets.split(", ")) <= set(listed), out
