"""Steps for pubkit cache: a repository's cache entries, and a build
command that counts its runs, so a scenario sees which units were made and
which reused."""

from __future__ import annotations

import json
import re
import sys

from pytest_bdd import given, parsers, scenarios, then, when

from conftest import pubkit

scenarios("../features/cache.feature")

# The build: copies a unit's data and build.py into its pack, and counts
# its runs in runs.txt.
BUILD = ("import sys, pathlib; a, src = sys.argv[1], pathlib.Path(sys.argv[2]); "
         "out = pathlib.Path('out') / (a + '.pack'); out.parent.mkdir(exist_ok=True); "
         "out.write_text(''.join(p.read_text() for p in sorted((src / a).iterdir()))"
         " + pathlib.Path('build.py').read_text()); "
         "runs = pathlib.Path('runs.txt'); runs.write_text(runs.read_text() + 'x' if runs.exists() else 'x')")


def _config(repo, change):
    path = repo / "pubkit.json"
    config = json.loads(path.read_text()) if path.exists() else {"kind": "utility"}
    change(config)
    path.write_text(json.dumps(config))


@given(parsers.parse('a repository whose cache entry "{name}" is made per unit from "{unit}" '
                     'with the input "{input}" and the tool "{tool}"'))
def cache_entry(repo, run, name, unit, input, tool):
    assert run(pubkit(), "init", "--kind=utility", cwd=repo).code == 0
    (repo / "tool.txt").write_text("1")
    _config(repo, lambda c: c.update(cache={name: {
        "path": ".packcache", "unit": unit, "inputs": [input], "tools": [tool]}}))


@given(parsers.parse('the cache entry "{name}" is made per unit from "{unit}"'))
def unit_elsewhere(repo, name, unit):
    _config(repo, lambda c: c["cache"][name].update(unit=unit))


@when(parsers.parse('the cache entry "{name}" has the version "{version}"'))
def entry_version(repo, name, version):
    _config(repo, lambda c: c["cache"][name].update(version=version))


@given(parsers.parse('the cache entry "{name}" keeps "{path}" with refresh "{refresh}"'))
def fetched_entry(repo, name, path, refresh):
    _config(repo, lambda c: c["cache"].update({name: {"path": path, "refresh": refresh}}))


@given(parsers.parse('the cache entry "{name}" keeps "{path}" with no refresh or inputs'))
def loose_entry(repo, name, path):
    _config(repo, lambda c: c["cache"].update({name: {"path": path}}))


@given(parsers.parse('the cache entry "{name}" has the setting "{key}"'))
def odd_setting(repo, name, key):
    _config(repo, lambda c: c["cache"][name].update({key: True}))


@given(parsers.parse('today is "{day}"'))
def today(env, day):
    env["PUBKIT_TODAY"] = day


def _make(repo, run, env, area, src):
    return run(pubkit(), "cache", "run", "packs", "--unit", area, "--output", f"out/{area}.pack",
               "--", sys.executable, "-c", BUILD, area, str(src), cwd=repo, env=env)


@when(parsers.parse('I make the pack of "{area}"'), target_fixture="result")
def make_pack(repo, run, env, area):
    return _make(repo, run, env, area, repo / "data")


@when(parsers.parse('I make the pack of "{area}" from "{where}"'), target_fixture="result")
def make_pack_elsewhere(repo, run, env, area, where):
    return _make(repo, run, env, area, repo.parent / where)


@given(parsers.parse('the file "{path}" outside the repository reads "{text}"'))
def file_outside(repo, path, text):
    target = repo.parent / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text + "\n")


@when(parsers.parse('the tool reports version "{version}"'))
def tool_version(repo, version):
    (repo / "tool.txt").write_text(version)


@then(parsers.parse('the pack of "{area}" holds "{text}"'))
def pack_holds(repo, area, text):
    assert text in (repo / "out" / f"{area}.pack").read_text()


@then(parsers.parse("the command ran {count:d} time(s)"))
def ran(repo, count):
    assert len((repo / "runs.txt").read_text()) == count


def _key(result, name) -> str:
    return re.search(rf"^{name}: path=\S+ key=(\S+)", result.stdout, re.M).group(1)


@then(parsers.parse('I note the key of "{name}"'), target_fixture="noted")
def note_key(result, name):
    return _key(result, name)


@then(parsers.parse('the key of "{name}" is as noted'))
def key_same(result, noted, name):
    assert _key(result, name) == noted


@then(parsers.parse('the key of "{name}" is not as noted'))
def key_changed(result, noted, name):
    assert _key(result, name) != noted


@given(parsers.parse("the cache block is {block}"))
def cache_block(repo, run, block):
    assert run(pubkit(), "init", "--kind=utility", cwd=repo).code == 0
    _config(repo, lambda c: c.update(cache=json.loads(block)))


@given(parsers.parse('the cache entry "{name}" is made from the input "{input}"'))
def made_entry(repo, name, input):
    _config(repo, lambda c: c["cache"].update({name: {"path": f".{name}cache", "inputs": [input]}}))


@given(parsers.parse('the cache entry "{name}" is made from the inputs "{a}" and "{b}"'))
def made_entry_two(repo, name, a, b):
    _config(repo, lambda c: c["cache"].update({name: {"path": f".{name}cache", "inputs": [a, b]}}))


@when(parsers.parse('I make "{name}" into "{output}"'), target_fixture="result")
def make_entry(repo, run, env, name, output):
    write = f"import pathlib; p = pathlib.Path({output!r}); p.parent.mkdir(exist_ok=True); p.write_text('made')"
    return run(pubkit(), "cache", "run", name, "--output", output, "--", sys.executable, "-c", write,
               cwd=repo, env=env)


@given(parsers.parse('GitHub\'s step output goes to "{path}"'))
def github_output(repo, env, path):
    env["GITHUB_OUTPUT"] = str(repo / path)


@then(parsers.parse('"{path}" has the line "{line}"'))
def has_line(repo, path, line):
    assert line in (repo / path).read_text().splitlines(), (repo / path).read_text()
