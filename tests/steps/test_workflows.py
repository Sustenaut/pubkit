"""Steps parsing the workflows pubkit ships, as YAML, the way GitHub does."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import yaml

from pubkit import standard
from pytest_bdd import parsers, scenarios, then, when

scenarios("workflows.feature")

SOURCE = Path(__file__).resolve().parents[2]
WORKFLOWS = SOURCE / ".github" / "workflows"


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


@then("it takes an optional SONAR_TOKEN secret")
def sonar_secret(workflow):
    secrets = triggers(workflow)["workflow_call"]["secrets"]
    assert secrets["SONAR_TOKEN"]["required"] is False


@then(parsers.parse('its "{name}" job runs the pinned SonarQube scan only when the token is set'))
def sonar_job(workflow, name):
    job = workflow["data"]["jobs"][name]
    assert job["env"] == {"SONAR_TOKEN": "${{ secrets.SONAR_TOKEN }}"}
    steps = job["steps"]
    scan = next(s for s in steps if s.get("uses", "").startswith("SonarSource/sonarqube-scan-action@"))
    assert scan["if"] == "env.SONAR_TOKEN != ''"
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["if"] == "env.SONAR_TOKEN != ''"
    assert checkout["with"]["fetch-depth"] == 0
    assert any(s.get("if") == "env.SONAR_TOKEN == ''" and "::notice::" in s["run"] for s in steps)


@then("the scan names the project OWNER_REPO in the organization owner, lower-cased unless pubkit.json names it")
def sonar_project(workflow):
    steps = workflow["data"]["jobs"]["sonar"]["steps"]
    args = next(s for s in steps if "sonarqube-scan-action" in s.get("uses", ""))["with"]["args"]
    assert "-Dsonar.organization=${{ steps.settings.outputs.organization }}" in args
    settings = next(s for s in steps if s.get("id") == "settings")
    assert ".security.sonar.organization" in settings["run"]
    assert 'organization=${organization:-${OWNER,,}}' in settings["run"]
    assert "2>/dev/null || true)" in settings["run"]  # no pubkit.json is no failure
    assert settings["env"]["OWNER"] == "${{ github.repository_owner }}"
    assert "-Dsonar.projectKey=${{ github.repository_owner }}_${{ github.event.repository.name }}" in args


@then("the scan's layout comes before sonar-args and pubkit.json's sonar args")
def layout_first(workflow):
    steps = workflow["data"]["jobs"]["sonar"]["steps"]
    args = next(s for s in steps if "sonarqube-scan-action" in s.get("uses", ""))["with"]["args"].split()
    # The scanner takes the last value a property is given.
    assert (args.index("inputs.layout") < args.index("inputs.sonar-args")
            < args.index("steps.settings.outputs.args")), args
    assert "layout" in triggers(workflow)["workflow_call"]["inputs"]


@then("the scan waits for the quality gate, so a failing gate fails the job")
def sonar_gate(workflow):
    """Without the wait the scan only uploads, so `ci / passed` stays green
    while SonarQube Cloud's quality gate fails (publet#56). The timeout
    keeps a stuck analysis from holding the job for the default 5 minutes
    longer than it needs."""
    steps = workflow["data"]["jobs"]["sonar"]["steps"]
    args = next(s for s in steps if "sonarqube-scan-action" in s.get("uses", ""))["with"]["args"].split()
    assert "-Dsonar.qualitygate.wait=true" in args
    assert "-Dsonar.qualitygate.timeout=300" in args
    # Before the caller's properties, so a caller can still override it.
    assert args.index("-Dsonar.qualitygate.wait=true") < args.index("${{")


@then("every action it uses is pinned to a full commit hash, with its version in a comment")
def pinned(workflow):
    for line in workflow["text"].splitlines():
        match = re.match(r"\s*(?:- )?uses: (\S+)(.*)", line)
        if not match or match.group(1).startswith("./") or line.lstrip().startswith("#"):
            continue
        action, rest = match.groups()
        # pubrel's and pubkit's own reusable workflows too (SonarQube's S7637).
        assert re.search(r"@[0-9a-f]{40}$", action), f"not pinned to a commit: {line.strip()}"
        assert re.match(r"\s+# v\d+\.\d+\.\d+$", rest), f"no version comment: {line.strip()}"


@then("its setup input runs npm ci with --ignore-scripts")
def npm_ignore_scripts(workflow):
    setup = triggers(workflow)["workflow_call"]["inputs"]["setup"]["default"]
    assert setup.startswith("npm ci ") and "--ignore-scripts" in setup.split()


@then("its browser-setup input runs npx with --no-install")
def npx_no_install(workflow):
    setup = triggers(workflow)["workflow_call"]["inputs"]["browser-setup"]["default"]
    assert setup.startswith("npx --no-install ")


@then(parsers.parse('the "{name}" job hands its coverage report to the sonar scan as "{prop}"'))
def coverage_handed_over(workflow, name, prop):
    jobs = workflow["data"]["jobs"]
    upload = next(s for s in jobs[name]["steps"]
                  if s.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload["with"]["if-no-files-found"] == "ignore", upload
    inputs = triggers(workflow)["workflow_call"]["inputs"]
    report = "browser-coverage-report" if name == "browser" else "coverage-report"
    if report in inputs:
        assert upload["with"]["path"] == f"${{{{ inputs.{report} }}}}", upload
    needs = jobs["sonar"]["needs"]
    assert name in ([needs] if isinstance(needs, str) else needs), jobs["sonar"]
    download = next(s for s in jobs["sonar"]["steps"]
                    if s.get("uses", "").startswith("actions/download-artifact@"))
    wanted = download["with"].get("name") or download["with"]["pattern"]
    assert fnmatch.fnmatchcase(upload["with"]["name"], wanted), download
    assert "merge-multiple" not in download["with"], "reports of the same name would overwrite each other"
    assert download.get("continue-on-error") is True, download
    scan = next(s for s in jobs["sonar"]["steps"] if "sonarqube-scan-action" in s.get("uses", ""))
    assert f"-D{prop}=" in scan["with"]["args"], scan["with"]["args"]


@then("the sonar job waits for the browser job, and runs when it was skipped")
def sonar_waits_for_browser(workflow):
    jobs = workflow["data"]["jobs"]
    sonar = jobs["sonar"]
    assert "browser" in sonar["needs"], sonar["needs"]
    tests = next(n for n in sonar["needs"] if n != "browser")
    assert sonar["if"] == (
        f"always() && needs.{tests}.result == 'success' && "
        "contains(fromJSON('[\"success\", \"skipped\"]'), needs.browser.result)"), sonar["if"]
    upload = next(s for s in jobs["browser"]["steps"]
                  if s.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload["with"]["path"] == "${{ inputs.browser-coverage-report }}", upload


@then("the functional job runs make coverage in place of make functional when given a SONAR_TOKEN")
def coverage_in_place(workflow):
    job = workflow["data"]["jobs"]["functional"]
    assert job["env"]["COVERAGE"] == "${{ secrets.SONAR_TOKEN != '' }}", job["env"]
    loop = next(s for s in job["steps"] if "for target in" in s.get("run", ""))
    assert 'if [[ "$target" = functional && "$COVERAGE" = true ]]; then target=coverage; fi' in loop["run"]
    shipped = (SOURCE / "src" / "pubkit" / "templates" / "rust" / "pubkit.mk").read_text()
    assert re.search(r"^coverage: .*##", shipped, re.M), "pubkit.mk has no coverage target"
    assert re.search(r"^\.PHONY: .*\bcoverage\b", shipped, re.M), "coverage is not phony"


@then("the token itself never reaches the functional job")
def token_kept_out(workflow):
    job = workflow["data"]["jobs"]["functional"]
    text = yaml.safe_dump(job)
    assert "secrets.SONAR_TOKEN }}" not in text, "the functional job is given the token"


@then(parsers.parse('its "{name}" job runs "{check}" on every event'))
def audit_job(workflow, name, check):
    job = workflow["data"]["jobs"][name]
    assert "if" not in job
    assert check in yaml.safe_dump(job["steps"])
    assert name in workflow["data"]["jobs"]["passed"]["needs"]


@then("no input turns the audit job off")
def audit_unconditional(workflow):
    steps = workflow["data"]["jobs"]["audit"]["steps"]
    assert not any("if" in step for step in steps)


@then("its fuzz job reads security.fuzz.rust from pubkit.json on a scheduled run")
def fuzz_setting(workflow):
    run = next(s["run"] for s in workflow["data"]["jobs"]["fuzz"]["steps"]
               if "fuzz-smoke" in s.get("run", ""))
    assert '"$EVENT" = schedule' in run and ".security.fuzz" in run and ".rust" in run
    assert "pubkit.json" in run and "$LONG" in run and "seconds=$SHORT" in run


@then(parsers.parse('its "{name}" job sets PUBKIT_FUZZ_RUNS before its tests, '
                    'from security.fuzz.{language} on a scheduled run'))
def property_runs(workflow, name, language):
    data = workflow["data"]
    steps = data["jobs"][name]["steps"]
    at = next(i for i, s in enumerate(steps) if "PUBKIT_FUZZ_RUNS" in s.get("run", ""))
    run = steps[at]["run"]
    assert '"$EVENT" = schedule' in run and ".security.fuzz" in run and f".{language}" in run
    assert "runs=$SHORT" in run and "runs=$LONG" in run and '>> "$GITHUB_ENV"' in run
    assert steps[at]["env"]["SHORT"] == "${{ inputs.fuzz-runs }}"
    assert steps[at]["env"]["LONG"] == "${{ inputs.fuzz-runs-scheduled }}"
    assert steps.index(next(s for s in steps if s.get("run") == "${{ inputs.test }}")) > at
    inputs = data[True]["workflow_call"]["inputs"]
    assert inputs["fuzz-runs"]["default"] == "100"
    assert inputs["fuzz-runs-scheduled"]["default"] == "10000"


@then(parsers.parse('its "{name}" job runs dependency review on public pull requests only'))
def review_job(workflow, name):
    job = workflow["data"]["jobs"][name]
    assert job["if"] == "github.event_name == 'pull_request' && !github.event.repository.private"
    assert job["steps"][0]["uses"].startswith("actions/dependency-review-action@")
    assert name in workflow["data"]["jobs"]["passed"]["needs"]


@then("its passed job lets the review job be skipped")
def review_may_skip(workflow):
    run = workflow["data"]["jobs"]["passed"]["steps"][0]["run"]
    assert '.key == "review"' in run or '.result == "skipped"' in run


@then(parsers.parse('it takes a required "{name}" secret and reads the repository\'s contents only'))
def board_secret(workflow, name):
    assert triggers(workflow)["workflow_call"]["secrets"][name]["required"] is True
    assert workflow["data"]["permissions"] == {"contents": "read"}
    assert all("permissions" not in job for job in workflow["data"]["jobs"].values())


@then("it runs pubkit board with the token as GITHUB_TOKEN")
def board_runs(workflow):
    steps = workflow["data"]["jobs"]["board"]["steps"]
    step = next(s for s in steps if "pubkit board" in s.get("run", ""))
    assert step["env"]["GITHUB_TOKEN"] == "${{ secrets.token }}"
    assert step["shell"] == "bash"


@then(parsers.parse('it uploads board.json as the "{name}" artifact, kept 90 days, and nothing else runs'))
def board_artifact(workflow, name):
    steps = workflow["data"]["jobs"]["board"]["steps"]
    upload = steps[-1]
    assert upload["uses"].startswith("actions/upload-artifact@")
    assert upload["with"]["name"] == name and upload["with"]["retention-days"] == 90
    assert "site/board.json" in upload["with"]["path"]
    uses = [s["uses"].split("@")[0] for s in steps if "uses" in s]
    assert uses == ["actions/checkout", "actions/setup-python", "actions/upload-artifact"]
    assert list(workflow["data"]["jobs"]) == ["board"]


@then("without a token it warns, unless the pull request comes from a fork")
def sonar_warns(workflow):
    steps = workflow["data"]["jobs"]["sonar"]["steps"]
    step = next(s for s in steps if s.get("if") == "env.SONAR_TOKEN == ''")
    assert "::warning::" in step["run"] and "secrets: inherit" in step["run"]
    assert "::notice::" in step["run"]
    assert step["env"]["FORK"] == "${{ github.event.pull_request.head.repo.fork || false }}"
    assert "${{" not in step["run"]  # nothing in the message is an expression
