"""Steps for `pubrules gherkin`, against a scratch utility whose suite is
Gherkin, changed one way at a time."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then

scenarios("gherkin.feature")

FEATURE = """Feature: Adding
  Scenario: Two and two
    Then two and two make four
"""
STEPS = '''from pytest_bdd import scenarios, then

scenarios("adding.feature")


@then("two and two make four")
def four():
    assert 2 + 2 == 4
'''
TESTS = {
    "a plain test function": "def test_adds():\n    assert 2 + 2 == 4\n",
    "a test class": "class TestAdding:\n    def test_adds(self):\n        assert 2 + 2 == 4\n",
    "a unittest test case": "import unittest\nclass Adding(unittest.TestCase):\n    def test_adds(self):\n"
                            "        self.assertEqual(2 + 2, 4)\n",
    "a binding that tests too": "from pytest_bdd import scenario\n\n\n"
                                "@scenario('adding.feature', 'Two and two')\ndef test_adds():\n"
                                "    assert 2 + 2 == 4\n",
    "a node:test test": "import test from 'node:test';\ntest('adds', () => {});\n",
    "a shell script": "#!/bin/sh\n[ \"$(expr 2 + 2)\" = 4 ]\n",
    "a fixture and a step": "import pytest\nfrom pytest_bdd import given\n\n\n@pytest.fixture\n"
                            "def testdata():\n    return 4\n\n\n@given('a number')\ndef test_number():\n"
                            "    return 4\n",
    "a fake server": "export function fakeGithub() { return {}; }\n",
    "a scenario": FEATURE.replace("Adding", "Not bound"),
    "cucumber steps that use node:test's mock": "import { mock } from 'node:test';\n"
                                                "import { Then } from '@cucumber/cucumber';\n"
                                                "Then('it adds', () => { mock.fn(); });\n",
}


@pytest.fixture
def repo(tmp_path):
    """A scratch repository with nothing in it: not conftest's, which holds
    the suite init seeds."""
    root = tmp_path / "repo"
    root.mkdir()
    return root


def write(repo, path, text):
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text)


@given("a utility whose suite is Gherkin")
def gherkin_suite(repo, env):
    write(repo, "pubrules.json", json.dumps({"kind": "utility", "ci": {}}) + "\n")
    write(repo, "tests/pytest.ini", "[pytest]\nbdd_features_base_dir = features/\n")
    write(repo, "tests/features/adding.feature", FEATURE)
    write(repo, "tests/steps/test_adding.py", STEPS)
    env["PUBRULES_TODAY"] = "2026-11-16"


@given("the suite's feature file is removed")
def feature_removed(repo):
    (repo / "tests/features/adding.feature").unlink()
    (repo / "tests/steps/test_adding.py").unlink()


@given(parsers.parse('the file "{path}" holds {test}'))
def holds(repo, path, test):
    write(repo, path, TESTS[test])


@given("the suite binds the whole features directory")
def binds_directory(repo):
    path = repo / "tests/steps/test_adding.py"
    path.write_text(path.read_text().replace('scenarios("adding.feature")', 'scenarios(".")'))


@given(parsers.parse('package.json lists "{package}" as a dev dependency'))
def dev_dependency(repo, package):
    write(repo, "package.json", json.dumps({"devDependencies": {package: "12.2.0"}}))


@given(parsers.parse('the file "{path}" lists "{package}" as a dev dependency'))
def dev_dependency_of(repo, path, package):
    write(repo, path, json.dumps({"devDependencies": {package: "1.0.0"}}))


@given(parsers.parse('pubrules.json declares an exception for "{rule}" until "{until}"'))
def exception(repo, rule, until):
    config = json.loads((repo / "pubrules.json").read_text())
    config["exceptions"] = [{"rule": rule, "reason": "moving to scenarios", "until": until}]
    write(repo, "pubrules.json", json.dumps(config) + "\n")


@given("pubrules' templates hold a compiled copy of a seeded .py file, as pip leaves one")
def compiled_template(repo, ctx):
    """What `pip install` does to the installed wheel: a __pycache__ beside
    templates/common/tests/steps/test_repository.py, which is not text."""
    from importlib import resources  # noqa: PLC0415
    steps = Path(str(resources.files("pubrules") / "templates" / "common" / "tests" / "steps"))
    cache = steps / "__pycache__"
    made = not cache.exists()
    cache.mkdir(exist_ok=True)
    stale = cache / "test_repository.cpython-0.pyc"
    stale.write_bytes(b"\x00\xf7\x0d\x0a binary")
    write(repo, "pubrules.json", json.dumps({"kind": "utility", "ci": {}}) + "\n")

    @ctx.cleanup
    def tidy():
        stale.unlink(missing_ok=True)
        if made:
            cache.rmdir()


@then("it reads every template, with no traceback")
def no_traceback(result):
    assert "Traceback" not in result.stderr and "UnicodeDecodeError" not in result.stderr, result.stderr
