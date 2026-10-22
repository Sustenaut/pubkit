"""Steps driving the plugin through pytester: each scenario builds a
throwaway suite, runs it in a subprocess, and judges what pytest reports."""

from __future__ import annotations

import re
import stat
import sys

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("plugin.feature")

# The steps every throwaway suite gets besides the plugin's: they exercise
# `run`, `ctx` and `binary`, and record cleanups where the outer scenario
# can read them.
INNER_STEPS = '''
import os
from pathlib import Path
from pytest_bdd import given, parsers, scenarios, then, when

scenarios(".")

@when(parsers.re(r'I run (?P<program>\\w+)(?: "(?P<arg>[^"]*)")?'), target_fixture="result")
def run_program(run, program, arg):
    return run(program, *([arg] if arg else []))

@when(parsers.parse('I run the "{name}" binary'), target_fixture="result")
def run_binary(run, binary, name):
    return run(binary(name))

@given(parsers.parse('cleanups "{a}" and "{b}"'))
def cleanups(ctx, a, b):
    log = Path(os.environ["CLEANUP_LOG"])
    for name in (a, b):
        ctx.cleanup(lambda name=name: log.open("a").write(name + ","))

@then("it fails anyway")
def fails_anyway():
    raise AssertionError("on purpose")

@then("a property holds for every integer")
def every_integer():
    from hypothesis import given, strategies as st
    tried = []

    @given(st.integers())
    def check(n):
        tried.append(n)

    check()
    Path(os.environ["CLEANUP_LOG"]).write_text(str(len(tried)))
'''


@pytest.fixture
def outer(pytester, tmp_path, monkeypatch):
    monkeypatch.setenv("CLEANUP_LOG", str(tmp_path / "cleanups"))
    monkeypatch.delenv("TOOL_BIN_DIR", raising=False)
    return {"pytester": pytester, "log": tmp_path / "cleanups"}


@given("a suite whose feature reads:")
def suite(outer, docstring):
    p = outer["pytester"]
    p.makeini("[pytest]\nbdd_features_base_dir = features/\naddopts = --strict-markers\n")
    (p.path / "features").mkdir()
    (p.path / "features" / "inner.feature").write_text(docstring)
    p.makepyfile(test_inner=INNER_STEPS)


@given(parsers.parse('a "{name}" program in a directory named by {var}'))
def program(outer, tmp_path, monkeypatch, name, var):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    tool = bindir / name
    tool.write_text(f"#!{sys.executable}\nprint('{name} ran')\n")
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv(var, str(bindir))


@when("the suite runs", target_fixture="outcome")
def runs(outer):
    return outer["pytester"].runpytest_subprocess()


@when(parsers.parse('the suite runs with "{a}" "{b}"'), target_fixture="outcome")
def runs_with(outer, a, b):
    return outer["pytester"].runpytest_subprocess(a, b)


@when(parsers.parse('the suite runs with PUBKIT_FUZZ_RUNS "{runs}"'), target_fixture="outcome")
def runs_fuzzing(outer, monkeypatch, runs):
    monkeypatch.setenv("PUBKIT_FUZZ_RUNS", runs)
    return outer["pytester"].runpytest_subprocess()


@then(parsers.parse("the property tried {n:d} inputs"))
def tried(outer, n):
    assert outer["log"].read_text() == str(n)


@then(parsers.parse('pytest stops with "{text}"'))
def stops(outcome, text):
    assert outcome.ret != 0
    assert text in "\n".join(outcome.errlines), "\n".join(outcome.errlines)


def count(outcome, key):
    return outcome.parseoutcomes().get(key, 0)


@then(parsers.re(r"(?P<n>\d+) scenarios? (?P<verb>pass|passes|fail|fails|is deselected|are deselected)"))
def tally(outcome, n, verb):
    key = {"pass": "passed", "passes": "passed", "fail": "failed", "fails": "failed"}.get(
        verb, "deselected")
    assert count(outcome, key) == int(n), "\n".join(outcome.outlines)
    if key == "passed":
        assert count(outcome, "failed") == 0, "\n".join(outcome.outlines)


@then(parsers.parse('the report shows "{text}"'))
def report_shows(outcome, text):
    report = "\n".join(outcome.outlines)
    assert text.replace("\\\\", "\\") in report, report


@then(parsers.parse('the cleanups ran as "{order}"'))
def cleanups_ran(outer, order):
    assert re.sub(",$", "", outer["log"].read_text()) == order
