"""Steps for security.py: the Dependabot file pubkit sync writes and the
SonarQube Cloud properties, and how the workflows read them."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from pytest_bdd import given, parsers, scenarios, then, when

from pubkit import nightly, security

scenarios("security.feature")

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


@given(parsers.parse("the security settings '{text}'"), target_fixture="config")
def config(text):
    return json.loads(text)


def entries(config):
    return yaml.safe_load(security.dependabot(config))["updates"]


@then(parsers.parse('dependabot.yml updates "{ecosystem}" at "{directory}" "{interval}"'))
def updates(config, ecosystem, directory, interval):
    assert {"package-ecosystem": ecosystem, "directory": directory,
            "schedule": {"interval": interval}}.items() <= next(
        e for e in entries(config) if e["package-ecosystem"] == ecosystem).items()


@then(parsers.parse('every dependabot.yml entry has "{key}" {value:d}'))
def every_entry(config, key, value):
    assert all(e[key] == value for e in entries(config))


def ignored(config, ecosystem):
    entry = next(e for e in entries(config) if e["package-ecosystem"] == ecosystem)
    return [i["dependency-name"] for i in entry.get("ignore", [])]


@then(parsers.re(r'the "(?P<ecosystem>[\w-]+)" entry ignores (?P<names>.+)'))
def entry_ignores(config, ecosystem, names):
    assert ignored(config, ecosystem) == re.findall(r'"([^"]+)"', names)


def matched(config, dependency):
    """Whether an ignore matches, as Dependabot reads one: `*` is any run of
    characters, slashes included, and case does not count."""
    return any(re.fullmatch(".*".join(map(re.escape, name.split("*"))), dependency, re.I)
               for name in ignored(config, "github-actions"))


@then(parsers.parse('Dependabot would not bump "{dependency}"'))
def not_bumped(config, dependency):
    assert matched(config, dependency)


@then(parsers.parse('Dependabot would bump "{dependency}"'))
def bumped(config, dependency):
    assert not matched(config, dependency)


@then("pubkit writes no dependabot.yml")
def no_file(config):
    assert security.dependabot(config) is None


@then(parsers.parse('reading them stops with "{message}"'))
def stops(repo, config, message):
    (repo / "pubkit.json").write_text(json.dumps(config))
    with pytest.raises(SystemExit) as stop:
        nightly.settings(repo)
    assert message in str(stop.value)


@then(parsers.parse('"pubkit.json" exists beside "{path}" naming "{ecosystem}"'))
def written(repo, path, ecosystem):
    assert (repo / "pubkit.json").is_file()
    assert ecosystem in (repo / path).read_text()


@when(parsers.parse('I parse the workflow "{name}"'), target_fixture="workflow")
def parse(name):
    return yaml.safe_load((WORKFLOWS / name).read_text())


@then("its sonar job reads security.sonar.args from pubkit.json into the scan")
def sonar_reads(workflow):
    steps = workflow["jobs"]["sonar"]["steps"]
    read = next(s for s in steps if s.get("id") == "settings")
    scan = next(s for s in steps if "sonarqube-scan-action" in s.get("uses", ""))
    assert ".security.sonar.args" in read["run"] and "pubkit.json" in read["run"]
    assert scan["with"]["args"].rstrip().endswith("${{ steps.settings.outputs.args }}")
