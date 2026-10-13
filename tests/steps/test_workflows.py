"""Steps parsing the workflows pubkit ships, as YAML, the way GitHub does."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from pubkit import standard
from pytest_bdd import parsers, scenarios, then, when

scenarios("workflows.feature")

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


@when(parsers.parse('I parse the workflow "{workflow}"'), target_fixture="workflow")
def parse(workflow):
    text = (WORKFLOWS / workflow).read_text()
    return {"text": text, "data": yaml.safe_load(text)}


def triggers(workflow):
    # YAML reads the bare key `on` as the boolean true.
    data = workflow["data"]
    return data.get("on", data.get(True))


@then("it is called with workflow_call")
def called(workflow):
    assert "workflow_call" in triggers(workflow)


@then("every input has only a type, a default, a description or required")
def inputs_well_formed(workflow):
    inputs = triggers(workflow)["workflow_call"]["inputs"]
    for name, spec in inputs.items():
        extra = set(spec) - {"type", "default", "description", "required"}
        assert not extra, f"input {name} has unexpected keys {extra}"


@then("every job the workflow runs uses only its declared inputs")
def inputs_declared(workflow):
    declared = set(triggers(workflow)["workflow_call"]["inputs"])
    used = set(re.findall(r"inputs\.([A-Za-z0-9_-]+)", workflow["text"]))
    assert used <= declared, f"undeclared inputs used: {used - declared}"


@then(parsers.parse('its "{name}" job always runs and needs every other job'))
def aggregate(workflow, name):
    jobs = workflow["data"]["jobs"]
    job = jobs[name]
    assert job.get("if") == "always()", job.get("if")
    assert set(job["needs"]) == set(jobs) - {name}, job["needs"]


@then("every job pubkit required plans for reads its targets from the plan job")
def jobs_read_plan(workflow):
    jobs = workflow["data"]["jobs"]
    outputs = jobs["plan"]["outputs"]
    for job in standard.JOBS:
        assert job in outputs, f"plan does not output {job}"
        assert f"needs.plan.outputs.{job}" in workflow["text"], f"no job reads {job}"
        assert "plan" in jobs[job]["needs"], f"{job} does not need plan"


@then("the gates and functional inputs only add targets")
def inputs_only_add(workflow):
    inputs = triggers(workflow)["workflow_call"]["inputs"]
    for name in ("gates", "functional"):
        assert inputs[name]["default"] == "", name
        assert f"EXTRA: '${{{{ inputs.{name} }}}}'" in workflow["text"], name
        assert f"REQUIRED: '${{{{ needs.plan.outputs.{name} }}}}'" in workflow["text"], name


@then("the plan job compares a pull request with its base and passes it to pubkit required")
def plan_passes_changes(workflow):
    steps = workflow["data"]["jobs"]["plan"]["steps"]
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout"))
    assert checkout.get("with", {}).get("fetch-depth") == 2, checkout
    diff = next(s for s in steps if "git diff --name-only HEAD^1 HEAD" in s.get("run", ""))
    assert diff.get("if") == "github.event_name == 'pull_request'", diff
    required = next(s for s in steps if s.get("id") == "required")
    for flag in ("--event", "--head-ref", "--changed-files"):
        assert flag in required["run"], flag
    assert required["env"] == {"EVENT": "${{ github.event_name }}",
                               "HEAD_REF": "${{ github.head_ref }}"}
