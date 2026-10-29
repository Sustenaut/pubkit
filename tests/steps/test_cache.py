"""Steps for pubkit cache: a repository's rules, and a build command that
counts its runs, so a scenario sees which units were made and which reused."""

from __future__ import annotations

import json
import re
import sys

from pytest_bdd import given, parsers, scenarios, then, when

from conftest import pubkit

scenarios("../features/cache.feature")

# The build: copies a unit's data and build.py into its pack, and counts
# its runs in runs.txt. The tool is a script whose --version reads tool.txt.
BUILD = ("import sys, pathlib; a = sys.argv[1]; "
         "out = pathlib.Path('out') / (a + '.pack'); out.parent.mkdir(exist_ok=True); "
         "out.write_text(''.join(p.read_text() for p in sorted(pathlib.Path('data', a).iterdir()))"
         " + pathlib.Path('build.py').read_text()); "
         "runs = pathlib.Path('runs.txt'); runs.write_text(runs.read_text() + 'x' if runs.exists() else 'x')")


def _config(repo, change=None):
    path = repo / "pubkit.json"
    config = json.loads(path.read_text()) if path.exists() else {"kind": "utility"}
    if change:
        change(config)
    path.write_text(json.dumps(config))


@given(parsers.parse('a repository whose cache rules make one pack per area from "{unit}" with the shared "{shared}"'))
def cache_rules(repo, unit, shared):
    (repo / "tool.txt").write_text("1")
    _config(repo, lambda c: c.update(cache={"outputs": {"packs": {
        "dir": ".packcache", "unit": [unit], "shared": [shared, "tool: cat tool.txt"]}}}))


@given(parsers.parse('the cache rules keep "{path}" for a "{refresh}"'))
def keep_input(repo, path, refresh):
    _config(repo, lambda c: c["cache"].update(inputs={path: {"path": path, "refresh": refresh}}))


@given(parsers.parse('the cache rules have the setting "{dotted}"'))
def odd_setting(repo, dotted):
    *parents, last = dotted.split(".")

    def change(c):
        node = c["cache"]
        for p in parents:
            node = node[p]
        node[last] = True
    _config(repo, change)


@given(parsers.parse('today is "{day}"'))
def today(env, day):
    env["PUBKIT_TODAY"] = day


@when(parsers.parse('I make the pack of "{area}"'), target_fixture="result")
def make_pack(repo, run, env, area):
    return run(pubkit(), "cache", "run", "packs", "--unit", f"area={area}", "--output", f"out/{area}.pack",
               "--", sys.executable, "-c", BUILD, area, cwd=repo, env=env)


@when(parsers.parse('the tool reports version "{version}"'))
def tool_version(repo, version):
    (repo / "tool.txt").write_text(version)


@then(parsers.parse('the pack of "{area}" holds "{text}"'))
def pack_holds(repo, area, text):
    assert text in (repo / "out" / f"{area}.pack").read_text()


@then(parsers.parse("the command ran {count:d} time(s)"))
def ran(repo, count):
    assert len((repo / "runs.txt").read_text()) == count


def _inputs_key(result) -> str:
    return re.search(r"^inputs-key=(\S+)$", result.stdout, re.M).group(1)


@then("I note the inputs key", target_fixture="noted")
def note_key(result):
    return _inputs_key(result)


@then("the inputs key is as noted")
def key_same(result, noted):
    assert _inputs_key(result) == noted


@then("the inputs key is not as noted")
def key_changed(result, noted):
    assert _inputs_key(result) != noted
