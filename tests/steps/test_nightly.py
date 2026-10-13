"""Steps for the nightly: `pubkit pins`, the parts of `pubkit crates` and
`pubkit nightly` that need no network, the workflow and its script, and
`pubkit new` without a key."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml
from pytest_bdd import parsers, scenarios, then, when

from pubkit import conform, new, nightly

scenarios("nightly.feature")

GITHUB = Path(__file__).resolve().parents[2] / ".github"
SCRIPT = GITHUB / "nightly" / "update.sh"


@then("it prints nothing")
def prints_nothing(result):
    out = result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout
    assert out.strip() == "", out


@when(parsers.parse('cargo vet reports "{first}" and "{second}"'), target_fixture="skipped")
def vet_reports(first, second):
    return nightly.failing("\n".join(s.replace('\\"', '"') for s in (first, second)))


@then(parsers.parse('the crates skipped are "{names}"'))
def crates_skipped(skipped, names):
    assert skipped == names.split(", ")


@when(parsers.parse('cargo update --dry-run says "{a}", "{b}" and "{c}"'),
      target_fixture="tried")
def dry_run(a, b, c):
    return nightly.upgrades("\n".join(f"    {line}" for line in (a, b, c)))


@then(parsers.parse('the upgrades tried are "{expected}"'))
def upgrades_tried(tried, expected):
    assert [f"{c} {a} -> {b}" for c, a, b in tried] == expected.split(", ")


@when(parsers.parse('a failing cargo vet ends with "{header}" and "{line}"'),
      target_fixture="reason")
def vet_fails(header, line):
    return nightly.recommended(f"Vetting Failed!\n  zeroize:1.9.1 missing\n{header}\n    {line}\n")


@then(parsers.parse('the reason starts at "{start}" and names "{line}"'))
def reason(reason, start, line):
    assert reason.startswith(start) and line in reason and "Vetting Failed" not in reason


@when(parsers.parse('the nightly writes a body with "{crate}" skipped because "{why}"'),
      target_fixture="body")
def writes_body(crate, why):
    return nightly.body(["pubkit 0.8.0 -> 0.9.0"], ["syn 3.0.6 -> 3.0.9"],
                        [{"crate": crate, "from": "1.9.0", "to": "1.9.1", "why": why}])


@then("the body says nothing merges it")
def nothing_merges(body):
    assert "Nothing merges it" in body


@then(parsers.parse('the body shows "{heading}" with "{why}"'))
def body_shows(body, heading, why):
    assert f"<summary>{heading}</summary>" in body
    assert why in body.split(heading, 1)[1]


@when(parsers.parse('I parse the workflow "{name}"'), target_fixture="workflow")
def parse(name):
    text = (GITHUB / "workflows" / name).read_text()
    return {"text": text, "data": yaml.safe_load(text), "script": SCRIPT.read_text()}


@then("it runs on a schedule and by hand")
def scheduled(workflow):
    on = workflow["data"].get("on", workflow["data"].get(True))
    assert "schedule" in on and "workflow_dispatch" in on


@then("neither it nor its script merges a pull request")
def merges_nothing(workflow):
    for text in (workflow["text"], workflow["script"]):
        assert not re.search(r"gh pr merge|--auto\b|automerge|enablePullRequestAutoMerge", text)


@then("its script updates an open pull request before opening one")
def updates_open(workflow):
    script = workflow["script"]
    assert script.index("gh pr list") < script.index("gh pr edit") < script.index("gh pr create")
    assert "--force origin" in script


@then("its script re-tests the pins alone when CI fails")
def retests(workflow):
    script = workflow["script"]
    assert 'HEAD~1:"$branch"' in script
    assert script.index('wait_ci "$head"') < script.index('wait_ci "$(git rev-parse HEAD~1)"')


@when(parsers.parse('I start the package "{name}" without a key'))
def start(repo, monkeypatch, name):
    monkeypatch.setattr(new, "check_tools", lambda: [])
    assert new.run(repo, name, f"PKG-{name.upper():<6.6}-10-2026".replace(" ", "X"), "",
                   key=False, lock=False) == 0


@then(parsers.parse('it conforms to the standard but for "{rule}"'))
def conforms_but(repo, rule):
    _, problems = conform.run(repo, json.loads((repo / "pubkit.json").read_text()))
    assert problems and all(p.startswith(f"{rule}:") for p in problems), problems
