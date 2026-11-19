"""Steps for security.py: the Dependabot file pubrules sync writes and the
SonarQube Cloud properties, and how the workflows read them."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml
from pytest_bdd import given, parsers, scenarios, then, when

from pubrules import nightly, security

scenarios("security.feature")

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


@given(parsers.parse("the security settings '{text}'"), target_fixture="config")
def config(text):
    return json.loads(text)


@when("pubrules writes dependabot.yml from them")
def writes(pubtest_ctx, config):
    """What it writes, as YAML reads it; nothing (null) when it writes no file."""
    def write():
        text = security.dependabot(config)
        read = None if text is None else yaml.safe_load(text)
        return text if read is None else read
    pubtest_ctx.attempt(write)


def entries(pubtest_ctx):
    assert pubtest_ctx.refused is None, pubtest_ctx.refused.message
    return pubtest_ctx.result["updates"]


@then(parsers.parse('dependabot.yml updates "{ecosystem}" at "{directory}" "{interval}"'))
def updates(pubtest_ctx, ecosystem, directory, interval):
    assert {"package-ecosystem": ecosystem, "directory": directory,
            "schedule": {"interval": interval}}.items() <= next(
        e for e in entries(pubtest_ctx) if e["package-ecosystem"] == ecosystem).items()


@then(parsers.parse('every dependabot.yml entry has "{key}" {value:d}'))
def every_entry(pubtest_ctx, key, value):
    assert all(e[key] == value for e in entries(pubtest_ctx))


def ignored(pubtest_ctx, ecosystem):
    entry = next(e for e in entries(pubtest_ctx) if e["package-ecosystem"] == ecosystem)
    return [i["dependency-name"] for i in entry.get("ignore", [])]


@then(parsers.parse('the "{ecosystem}" entry has "{key}" {value:d}'))
def entry_has(pubtest_ctx, ecosystem, key, value):
    entry = next(e for e in entries(pubtest_ctx) if e["package-ecosystem"] == ecosystem)
    assert entry[key] == value, entry


@then(parsers.parse('the "{ecosystem}" entry has no "{key}"'))
def entry_lacks(pubtest_ctx, ecosystem, key):
    entry = next(e for e in entries(pubtest_ctx) if e["package-ecosystem"] == ecosystem)
    assert key not in entry, entry


@then(parsers.re(r'the "(?P<ecosystem>[\w-]+)" entry ignores (?P<names>.+)'))
def entry_ignores(pubtest_ctx, ecosystem, names):
    assert ignored(pubtest_ctx, ecosystem) == re.findall(r'"([^"]+)"', names)


def matched(pubtest_ctx, dependency):
    """Whether an ignore matches, as Dependabot reads one: `*` is any run of
    characters, slashes included, and case does not count."""
    return any(re.fullmatch(".*".join(map(re.escape, name.split("*"))), dependency, re.I)
               for name in ignored(pubtest_ctx, "github-actions"))


@then(parsers.parse('Dependabot would not bump "{dependency}"'))
def not_bumped(pubtest_ctx, dependency):
    assert matched(pubtest_ctx, dependency)


@then(parsers.parse('Dependabot would bump "{dependency}"'))
def bumped(pubtest_ctx, dependency):
    assert not matched(pubtest_ctx, dependency)


@when("the nightly reads its settings from them")
def reads_settings(pubtest_ctx, repo, config):
    (repo / "pubrules.json").write_text(json.dumps(config))
    pubtest_ctx.attempt(lambda: nightly.settings(repo))


@then(parsers.parse('"pubrules.json" exists beside "{path}" naming "{ecosystem}"'))
def written(repo, path, ecosystem):
    assert (repo / "pubrules.json").is_file()
    assert ecosystem in (repo / path).read_text()


@when(parsers.parse('I parse the workflow "{name}"'), target_fixture="workflow")
def parse(name):
    return yaml.safe_load((WORKFLOWS / name).read_text())


@then("its sonar job reads security.sonar.args from pubrules.json into the scan")
def sonar_reads(workflow):
    steps = workflow["jobs"]["sonar"]["steps"]
    read = next(s for s in steps if s.get("id") == "settings")
    scan = next(s for s in steps if "sonarqube-scan-action" in s.get("uses", ""))
    assert ".security.sonar.args" in read["run"] and "pubrules.json" in read["run"]
    assert scan["with"]["args"].rstrip().endswith("${{ steps.settings.outputs.args }}")
