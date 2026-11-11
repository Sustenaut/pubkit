"""Steps for the CI `pubkit sync` writes from pubkit.json's ci block."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from pytest_bdd import given, parsers, scenarios, then, when

from pubkit import __version__, ci, cli, refs

scenarios("ci.feature")

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def generated(repo, name):
    path = repo / ".github" / "workflows" / name
    assert path.exists(), f"{name} was not written"
    return yaml.safe_load(path.read_text())


def jobs(repo, name):
    return generated(repo, name)["jobs"]


def triggers(data):
    # YAML reads the bare key `on` as the boolean true.
    return data.get("on", data.get(True))


KINDS = {"Node": "utility", "Python": "utility", "Rust": "rust", "service": "service"}


@then(parsers.parse('"{name}"\'s job "{job}" is given the tools "{text}"'))
def given_tools(repo, name, job, text):
    assert jobs(repo, name)[job]["with"]["tools"] == text


@then(parsers.parse('"{name}"\'s job "{job}" is given no tools'))
def given_no_tools(repo, name, job):
    assert "tools" not in jobs(repo, name)[job].get("with", {})


@then(parsers.parse('"{name}"\'s job "{job}" passes "{key}" as "{value}"'))
def passes(repo, name, job, key, value):
    assert jobs(repo, name)[job]["with"][key] == value


def _install_step(workflow):
    data = yaml.safe_load((WORKFLOWS / workflow).read_text())
    (step,) = [s for job in data["jobs"].values() for s in job.get("steps", [])
               if "--root" in s.get("run", "")]
    return step["run"]


TWO_TOOLS = json.dumps([{"crate": "one-cli", "git": "https://example.eu/one", "tag": "v1"},
                        {"crate": "two-cli", "git": "https://example.eu/two.git", "tag": "v2.0"}])


@when(parsers.parse('the "{workflow}" step that installs tools runs on two tools'),
      target_fixture="install")
def install_tools(tmp_path, workflow):
    return _run_install(tmp_path, workflow, 'echo "$*" >> "$ASKED"\n')


@when(parsers.parse('the "{workflow}" step that installs tools runs with cargo failing'),
      target_fixture="install")
def install_tools_failing(tmp_path, workflow):
    return _run_install(tmp_path, workflow, "exit 101\n")


def _run_install(tmp_path, workflow, cargo):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "cargo"
    fake.write_text("#!/bin/sh\n" + cargo)
    fake.chmod(0o755)
    asked = tmp_path / "asked"
    done = subprocess.run(  # GitHub's default for `run`: bash -e.
        ["bash", "-e", "-c", _install_step(workflow)], capture_output=True, text=True, check=False,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
             "HOME": str(tmp_path), "TOOLS": TWO_TOOLS, "ASKED": str(asked)})
    return {"code": done.returncode, "asked": asked, "home": tmp_path}


@then("it installed both with cargo at their tags, into the tools directory")
def installed(install):
    root = f"--root {install['home']}/.cargo-tools"
    assert install["code"] == 0
    assert install["asked"].read_text().splitlines() == [
        f"install --locked {root} --git https://example.eu/one --tag v1 one-cli",
        f"install --locked {root} --git https://example.eu/two.git --tag v2.0 two-cli"]


@then("the step fails")
def step_fails(install):
    assert install["code"] != 0


@then("every step that installs tests/requirements.lock does it through tests/install-lock")
def installs_retried():
    found = 0
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for job in (yaml.safe_load(path.read_text()).get("jobs") or {}).values():
            for step in job.get("steps") or []:
                for line in step.get("run", "").splitlines():
                    assert not ("pip install" in line and "requirements.lock" in line), f"{path.name}: {line}"
                    if line.strip().startswith("tests/install-lock "):
                        found += 1
    assert found >= 4


@then("every pip-audit is tried three times, each try after a failed one")
def audits_retried():
    found = 0
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for job in (yaml.safe_load(path.read_text()).get("jobs") or {}).values():
            tries = [s for s in job.get("steps") or [] if "gh-action-pip-audit" in s.get("uses", "")]
            if not tries:
                continue
            found += 1
            assert len(tries) == 3, path.name
            assert tries[0].get("continue-on-error") and tries[1].get("continue-on-error"), path.name
            assert tries[1]["if"] == f"steps.{tries[0]['id']}.outcome == 'failure'", path.name
            assert tries[2]["if"] == f"steps.{tries[1]['id']}.outcome == 'failure'", path.name
            assert not tries[2].get("continue-on-error"), path.name
            # Before the last try, each lock's release wheels move to the mirror.
            steps = job["steps"]
            between = steps[steps.index(tries[1]) + 1:steps.index(tries[2])]
            assert any("tests/install-lock --rewrite" in s.get("run", "") for s in between), path.name
    assert found >= 2


def _review_steps(workflow="job-review.yml"):
    data = yaml.safe_load((WORKFLOWS / workflow).read_text())
    return data["jobs"]["review"]["steps"]


def _review_step():
    return _review_steps()[-1]


def _default_purls():
    (step,) = [s for s in _review_steps() if s.get("id") == "exceptions"]
    return step["env"]["DEFAULT"]


@then("job-review.yml's license exceptions name every reusable workflow and action pubkit has, "
      "and pubrel's release workflow")
def review_exceptions():
    listed = {p.strip() for p in _default_purls().split(",")}
    wanted = {f"pkg:githubactions/Sustenaut/pubkit/.github/workflows/{path.name}"
              for path in WORKFLOWS.glob("*.yml") if "workflow_call" in (yaml.safe_load(path.read_text()).get(True) or {})}
    wanted |= {f"pkg:githubactions/Sustenaut/pubkit/actions/{path.parent.name}"
               for path in (WORKFLOWS.parents[1] / "actions").glob("*/action.yml")}
    wanted |= {"pkg:githubactions/Sustenaut/pubrel/.github/workflows/release.yml"}
    assert listed == wanted, (sorted(wanted - listed), sorted(listed - wanted))


@then("job-review.yml sets no other input, so advisories apply to everything")
def review_only_licenses():
    assert _review_step()["with"] == {"allow-dependencies-licenses": "${{ steps.exceptions.outputs.purls }}"}


DEPLOY = f"""on: push
jobs:
  scan:
    uses: Sustenaut/integrations/.github/workflows/scan.yml@0123456789abcdef0123456789abcdef01234567 # main
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5.1.0
      - uses: ./actions/local
      - uses: Sustenaut/pubkit/actions/cache@0123456789abcdef0123456789abcdef01234567 # v{__version__}
"""


@given("a deploy workflow that calls integrations' scan, pubkit's cache action, actions/checkout and a local action")
def deploy_workflow(repo):
    (repo / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    (repo / ".github" / "workflows" / "deploy.yml").write_text(DEPLOY)


@then(parsers.parse('"{path}" lists "{purl}"'))
def lists(repo, path, purl):
    assert purl in (repo / path).read_text().splitlines()


@then(parsers.parse('"{path}" names neither actions/checkout nor the local action'))
def names_neither(repo, path):
    text = (repo / path).read_text()
    assert "actions/checkout" not in text and "local" not in text, text


@then(parsers.parse('"{path}" is not there'))
def not_there(repo, path):
    assert not (repo / path).exists()


@when(parsers.re(r'the "(?P<workflow>[^"]+)" review exceptions step runs (?P<how>on a list holding another owner\'s purl|without a list)'),
      target_fixture="exceptions")
def run_exceptions(tmp_path, workflow, how):
    """The step's script under bash -eo pipefail, as GitHub runs it, in a checkout of its own."""
    (step,) = [s for s in _review_steps(workflow) if s.get("id") == "exceptions"]
    listing = tmp_path / ".github" / "dependency-review-licenses.txt"
    listing.parent.mkdir(exist_ok=True)
    listing.unlink(missing_ok=True)
    if how != "without a list":
        listing.write_text(
            "# a comment\npkg:githubactions/Sustenaut/pubkit/actions/cache\n"
            "pkg:githubactions/Sustenaut/integrations/.github/workflows/scan.yml\n"
            "pkg:githubactions/someone-else/thing/action\npkg:npm/left-pad\n")
    out = tmp_path / "output"
    out.unlink(missing_ok=True)
    done = subprocess.run(["bash", "-eo", "pipefail", "-c", step["run"]], cwd=tmp_path, capture_output=True,
                          text=True, check=False,
                          env={**os.environ, "OWNER": "Sustenaut", "DEFAULT": step["env"]["DEFAULT"],
                               "GITHUB_OUTPUT": str(out)})
    assert done.returncode == 0, done.stderr
    (line,) = out.read_text().splitlines()
    return line.removeprefix("purls=").split(",")


@then("it passes the owner's purls alone")
def owners_alone(exceptions):
    assert exceptions == ["pkg:githubactions/Sustenaut/pubkit/actions/cache",
                          "pkg:githubactions/Sustenaut/integrations/.github/workflows/scan.yml"]


@then("it passes pubkit's and pubrel's")
def pubkits(exceptions):
    assert exceptions == [p.strip() for p in _default_purls().split(",")]


@given(parsers.parse('the latest release of "{slug}" is "{tag}"'))
def latest_release(tmp_path, env, slug, tag):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "gh"
    fake.write_text(f'#!/bin/sh\n[ "$4" = "{slug}" ] || exit 1\nprintf "%s\\n" "{tag}"\n')
    fake.chmod(0o755)
    env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


@then(parsers.parse('"{command}" reported no tool moving'))
def no_tool_moved(result, command):
    out = result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout
    assert "ci.tools" not in out, out


@given(parsers.parse("a {language} repository whose ci block is '{text}'"))
def repository(repo, language, text):
    if language == "Node":
        (repo / "package.json").write_text("{}\n")
    if language == "Rust":
        # A crate with a library, which cargo metadata reads; nothing is built.
        (repo / "Cargo.toml").write_text(
            '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n')
        (repo / "src").mkdir()
        (repo / "src" / "lib.rs").write_text("")
    (repo / "pubkit.json").write_text(json.dumps({"kind": KINDS[language], "ci": json.loads(text)}))


@given(parsers.parse('pubkit.json excepts "{rule}"'))
def excepts(repo, rule):
    config = json.loads((repo / "pubkit.json").read_text())
    config["exceptions"] = [{"rule": rule, "reason": "not yet", "until": "2099-01-01"}]
    (repo / "pubkit.json").write_text(json.dumps(config))


@given(parsers.parse("the file \"{path}\" reads '{text}'"))
def file_reads_json(repo, path, text):
    (repo / path).write_text(text + "\n")


@then(parsers.parse('"{name}" runs the jobs "{expected}"'))
def runs(repo, name, expected):
    assert list(jobs(repo, name)) == expected.split(", ")


@then(parsers.parse('"{name}" calls the repository\'s own release workflow'))
def local_release(repo, name):
    job = jobs(repo, name)["release"]
    assert job["uses"] == "./.github/workflows/release.yml"
    assert job["needs"] == "passed"
    assert "with" not in job
    assert job["permissions"] == {"contents": "write"}
    assert job["secrets"] == APP_SECRETS


@then(parsers.parse('"{name}" has no release'))
def no_release(repo, name):
    assert "release" not in jobs(repo, name)


@then(parsers.parse('no workflow is written for "{event}"'))
def not_written(repo, event):
    assert not (repo / ".github" / "workflows" / f"{event}.yml").exists()


@then(parsers.parse('no job in "{name}" has a condition but passed'))
def unconditional(repo, name):
    for job, spec in jobs(repo, name).items():
        assert "if" not in spec or job == "passed", f"{name}: {job} has if: {spec.get('if')}"


UNTESTED = "${{ needs.tested.outputs.tested != 'true' }}"


@then(parsers.parse('"{name}" first asks whether the merge queue passed its commit in "{queued}"'))
def asks_tested(repo, name, queued):
    first, job = next(iter(jobs(repo, name).items()))
    assert first == "tested"
    assert job["uses"].startswith(f"{refs.OWNER}/pubkit/.github/workflows/job-tested.yml@")
    assert job["permissions"] == {"actions": "read", "contents": "read"}
    assert job["with"] == {"workflow": f".github/workflows/{queued}"}
    data, _ = declared("job-tested.yml")
    assert "workflow" in triggers(data)["workflow_call"]["inputs"]


GH = r"""#!/usr/bin/env python3
import json, os, subprocess, sys
args = sys.argv[1:]
assert args[0] == "api", args
path, jq = args[1], args[args.index("--jq") + 1] if "--jq" in args else None
answers = json.load(open(os.environ["ANSWERS"]))
body = next((v for k, v in answers.items() if path.split("?")[0].endswith(k)), None)
if body is None:
    sys.exit(f"no answer for {path}")
text = json.dumps(body)
if jq:
    text = subprocess.run(["jq", "-r", jq], input=text, capture_output=True, text=True, check=True).stdout
sys.stdout.write(text)
"""


@given(parsers.parse("GitHub holds {runs}"), target_fixture="github")
def github_holds(runs):
    """The answers job-tested's script gets: this commit ("queue") has tree T."""
    answers = {"/git/commits/queue": {"tree": {"sha": "T"}}, "/runs": {"workflow_runs": []}}
    listed = answers["/runs"]["workflow_runs"]
    for m in re.finditer(r'(pull request|queue) run (\d+) of commit "(\w+)"(?: with tree "(\w)")?', runs):
        kind, run, commit, tree = m.groups()
        listed.append({"id": int(run), "head_sha": commit,
                       "event": "pull_request" if kind == "pull request" else "merge_group"})
        if tree:
            answers[f"/git/commits/{commit}"] = {"tree": {"sha": tree}}
        jobs = [{"id": 900 + int(run), "name": "ci / tested", "conclusion": "success"}]
        answers[f"/runs/{run}/jobs"] = {"jobs": jobs}
        answers[f"/check-runs/{900 + int(run)}/annotations"] = []
        answers[f"/runs/{run}/artifacts"] = {"artifacts": [{"name": "coverage-test", "expired": "expired" in runs}]}
    took = re.search(r"took its tests from run (\d+)", runs)
    if took:
        answers["/git/commits/queue"] = {"tree": {"sha": "T"}}
        answers["/check-runs/912/annotations"] = [{"message": f"This commit's tree was tested in run {took[1]} of pr.yml"}]
    # Most recent first, as GitHub lists them.
    listed.sort(key=lambda r: -r["id"])
    return answers


@when(parsers.parse('job-tested runs on a {event} of commit "{commit}"'), target_fixture="tested")
def run_job_tested(tmp_path, github, event, commit):
    data, _ = declared("job-tested.yml")
    (step,) = data["jobs"]["tested"]["steps"]
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "gh").write_text(GH)
    (tmp_path / "bin" / "gh").chmod(0o755)
    (tmp_path / "answers.json").write_text(json.dumps(github))
    out = tmp_path / "output"
    done = subprocess.run(["bash", "-eo", "pipefail", "-c", step["run"]], capture_output=True, text=True, check=False,
                          env={**os.environ, "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
                               "ANSWERS": str(tmp_path / "answers.json"), "GITHUB_OUTPUT": str(out),
                               "REPO": "Sustenaut/example", "SHA": commit, "EVENT": event, "RUN_ID": "99",
                               "WORKFLOW": ".github/workflows/pr.yml"})
    assert done.returncode == 0, done.stderr
    return dict(line.split("=", 1) for line in out.read_text().splitlines())


@then(parsers.re(r'it says tested "(?P<flag>\w+)", run "(?P<run>\d*)"'))
def says_tested(tested, flag, run):
    assert tested.get("tested") == flag, tested
    assert tested.get("run-id", "") == run, tested


@then(parsers.parse('in "{name}" the "{job}" job runs whatever the queue passed'))
def runs_anyway(repo, name, job):
    spec = jobs(repo, name)[job]
    assert "if" not in spec, spec
    assert "tested" not in spec.get("needs", []), spec
    assert job in jobs(repo, name)["passed"]["needs"]


@then(parsers.parse('in "{name}" every job but tested, the scan, passed and release runs only when it did not'))
def skips_tested(repo, name):
    for job, spec in jobs(repo, name).items():
        if job in ("tested", "sonar", "passed", "release"):
            continue
        assert spec["if"] == UNTESTED, (job, spec.get("if"))
        assert spec["needs"][0] == "tested", (job, spec["needs"])


@then(parsers.parse('in "{name}" the scan runs only when it did not, as main\'s push scans the tree'))
def scan_skips(repo, name):
    spec = jobs(repo, name)["sonar"]
    assert spec["if"] == UNTESTED, spec.get("if")
    assert spec["needs"][0] == "tested", spec["needs"]
    assert "coverage-run" not in spec.get("with", {}), spec


@then(parsers.parse('in "{name}" the scan runs either way, reading the coverage of the queue\'s run '
                    'when it skipped the tests'))
def scan_either_way(repo, name):
    spec = jobs(repo, name).get("sonar")
    if spec is None:  # a service has no scan
        return
    assert spec["if"] == "${{ !failure() && !cancelled() }}"
    assert spec["needs"][0] == "tested"
    assert spec["with"]["coverage-run"] == "${{ needs.tested.outputs.run-id }}"
    assert spec["permissions"] == {"actions": "read", "contents": "read"}


@then(parsers.parse('in "{name}" passed takes a job skipped for the queue\'s run as passed'))
def passed_takes_skipped(repo, name):
    spec = jobs(repo, name)["passed"]
    assert spec["needs"] == list(jobs(repo, name))[:len(spec["needs"])]
    run = spec["steps"][0]["run"]
    check = re.search(r"jq -e --arg tested \"\$TESTED\" '(.*)'", run, re.S).group(1)

    def verdict(results, tested):
        proc = subprocess.run(["jq", "-e", "--arg", "tested", tested, check],
                              input=json.dumps(results), capture_output=True, text=True)
        return proc.returncode == 0

    names = spec["needs"]
    every = {n: {"result": "success"} for n in names}
    skipped = {**every, **{n: {"result": "skipped"} for n in names[1:]}}
    failed = {**every, names[-1]: {"result": "failure"}}
    assert verdict(every, "false") and verdict(every, "true")
    assert verdict(skipped, "true") and not verdict(skipped, "false")
    assert not verdict(failed, "true")
    assert not verdict({**every, "tested": {"result": "failure"}}, "false")
    assert spec["steps"][0]["env"]["TESTED"] == "${{ needs.tested.outputs.tested }}"


@then(parsers.parse('in "{name}" the release runs whenever passed succeeded'))
def release_after_passed(repo, name):
    spec = jobs(repo, name).get("release")
    if spec is None:  # a service has no release
        return
    assert spec["if"] == "${{ !cancelled() && needs.passed.result == 'success' }}"


@then(parsers.parse('in "{name}" the "{job}" job needs "{needs}"'))
def needs(repo, name, job, needs):
    assert jobs(repo, name)[job]["needs"] == needs.split(", ")


@then(parsers.parse('in "{name}" the "{job}" job is given "{key}" "{value}"'))
def given_input(repo, name, job, key, value):
    assert jobs(repo, name)[job]["with"][key] == value


@then(parsers.parse('"{name}" runs on the schedule "{cron}" and by hand'))
def scheduled(repo, name, cron):
    on = triggers(generated(repo, name))
    assert on["schedule"] == [{"cron": cron}]
    assert "workflow_dispatch" in on
    assert set(on) == {"schedule", "workflow_dispatch"}


@then(parsers.parse('"{name}" runs on pull requests and in the merge queue'))
def queued(repo, name):
    assert set(triggers(generated(repo, name))) == {"pull_request", "merge_group"}


@then(parsers.parse('"{name}" runs on pull requests alone'))
def pull_requests_alone(repo, name):
    assert set(triggers(generated(repo, name))) == {"pull_request"}


@then(parsers.parse('"{name}" runs in the merge queue alone'))
def queue_alone(repo, name):
    assert set(triggers(generated(repo, name))) == {"merge_group"}


@then(parsers.parse('"{name}" runs on pushes to main alone'))
def on_main(repo, name):
    assert triggers(generated(repo, name)) == {"push": {"branches": ["main"]}}


@when("I read pubkit's own ci.yml", target_fixture="own")
def own_ci():
    return yaml.safe_load((WORKFLOWS / "ci.yml").read_text())


@then("it runs on pull requests, in the merge queue and on pushes to main")
def own_triggers(own):
    on = triggers(own)
    assert set(on) == {"pull_request", "merge_group", "push"}
    assert on["push"] == {"branches": ["main"]}


@then("in the merge queue and on a push to main it skips the tests an earlier run passed on the same tree")
def own_shortcut(own):
    jobs_ = own["jobs"]
    tested = jobs_["tested"]
    assert tested["uses"] == "./.github/workflows/job-tested.yml"
    assert tested["if"] == "github.event_name != 'pull_request'"
    assert tested["with"] == {"workflow": ".github/workflows/ci.yml"}
    for job in ("functional", "audit", "osv", "cache-action"):
        assert jobs_[job]["needs"] == "tested", job
        assert jobs_[job]["if"] == "${{ !cancelled() && needs.tested.outputs.tested != 'true' }}", job
    assert jobs_["sonar"]["with"]["coverage-run"] == "${{ needs.tested.outputs.run-id }}"
    assert "needs.tested.outputs.tested == 'true'" in jobs_["release"]["if"]


@then("its wheel job runs only on a push to main")
def own_wheel(own):
    # The release itself tags from a push to main alone: pubrel's
    # release.yml runs `pubrel tag` only then. The wheel is pubkit's own.
    condition = own["jobs"]["wheel"]["if"]
    assert "github.event_name == 'push'" in condition
    assert "github.ref == 'refs/heads/main'" in condition


@given(parsers.parse('the repository has the directories "{names}"'))
def directories(repo, names):
    for name in names.split(", "):
        (repo / name).mkdir(parents=True, exist_ok=True)
        (repo / name / "README.md").write_text("")


@given(parsers.parse('the workflow "{name}" deploys "{folder}" with deploy-static'))
def deploys(repo, name, folder):
    path = repo / ".github" / "workflows" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("on: { push: { branches: [main] } }\njobs:\n  deploy:\n    runs-on: ubuntu-latest\n"
                    "    steps:\n      - run: make site\n"
                    "      - uses: Sustenaut/pubhost/actions/deploy-static@2196c741d3de6c92c6035c42435c6e517c8ba0ce # v0.1.0\n"
                    f"        with:\n          dir: {folder}\n          deploy-key: ${{{{ secrets.KEY }}}}\n")


@given(parsers.parse("pubkit.json's site block is '{text}'"))
def site_block(repo, text):
    config = json.loads((repo / "pubkit.json").read_text())
    config["site"] = json.loads(text)
    (repo / "pubkit.json").write_text(json.dumps(config))


@then(parsers.parse('"{name}" checks the site in "{folder}" after running "{build}", deploying nothing'))
def site_checked(repo, name, folder, build):
    data = generated(repo, name)
    job = data["jobs"]["site"]
    assert job["name"] == "ci / site"
    assert "site" in data["jobs"]["passed"]["needs"]
    steps = job["steps"]
    assert steps[0]["uses"].startswith("actions/checkout@")
    runs = [s["run"] for s in steps if "run" in s]
    assert runs == ([] if build == "nothing" else [build]), runs
    action = steps[-1]
    # The deploy-static the repository's deploy workflow pins, as it pins it.
    assert action["uses"] == "Sustenaut/pubhost/actions/deploy-static@2196c741d3de6c92c6035c42435c6e517c8ba0ce", action["uses"]
    assert "deploy-static@2196c741d3de6c92c6035c42435c6e517c8ba0ce # v0.1.0\n" in (
        repo / ".github" / "workflows" / f"{name}").read_text()
    assert action["with"]["dir"] == folder
    assert action["with"]["check-only"] == "true"
    assert not set(action["with"]) & {"deploy-key", "cloudflare-api-token"}


@then(parsers.parse('"{name}" has no site job'))
def no_site(repo, name):
    assert "site" not in generated(repo, name)["jobs"]


@then(parsers.parse('in "{name}" the site check is given "{key}" "{value}"'))
def site_given(repo, name, key, value):
    assert generated(repo, name)["jobs"]["site"]["steps"][-1]["with"][key] == value


@then(parsers.parse('in "{name}" the "{job}" job is given no "{key}"'))
def not_given(repo, name, job, key):
    assert key not in jobs(repo, name)[job].get("with", {})


@then(parsers.parse('"{name}" calls pubrel\'s release workflow at "{version}" with "{key}" "{value}"'))
def release(repo, name, version, key, value):
    job = jobs(repo, name)["release"]
    # By tag only when pubrel's tags could not be read; by commit otherwise.
    pubrel = re.escape(f"{refs.OWNER}/pubrel/.github/workflows/release.yml@")
    assert re.fullmatch(rf"{pubrel}([0-9a-f]{{40}}|v{re.escape(version)})", job["uses"]), job["uses"]
    text = (repo / ".github" / "workflows" / name).read_text()
    assert re.search(rf"release\.yml@(v|[0-9a-f]{{40}} # v){re.escape(version)}\n", text)
    assert job["with"]["pubrel-version"] == f"v{version}"
    assert job["with"][key] == value
    assert job["needs"] == "passed"


APP_SECRETS = {"RELEASE_APP_ID": "${{ secrets.RELEASE_APP_ID }}",
               "RELEASE_APP_KEY": "${{ secrets.RELEASE_APP_KEY }}"}


@then(parsers.re(r'in "pr.yml" and "main.yml" the release is (?P<given>given|not given) '
                 r'the release app\'s secrets$'))
def app_secrets(repo, given):
    for name in ("pr.yml", "main.yml"):
        release = jobs(repo, name)["release"]
        if given == "given":
            assert release["secrets"] == APP_SECRETS, (name, release)
        else:
            assert "secrets" not in release, (name, release)


@then(parsers.parse('"{name}" pins pubrel\'s release workflow to the commit pubkit records'))
def recorded_pubrel(repo, name):
    from pubkit.new import PUBREL_COMMIT, PUBREL_PIN  # noqa: PLC0415

    text = (repo / ".github" / "workflows" / name).read_text()
    assert f"release.yml@{PUBREL_COMMIT} # v{PUBREL_PIN}\n" in text, text


@when(parsers.parse('every pin reader reads "{line}"'), target_fixture="read")
def read_pin(line):
    from pubkit import conform, freshness, nightly  # noqa: PLC0415

    patterns = {"conform": conform.WORKFLOW_PIN, "bump": freshness.WORKFLOW_PIN,
                "nightly": nightly.CI_PIN, "release": refs.RELEASE_PIN}
    return {name: (m["owner"], m["version"], m["commit"]) for name, p in patterns.items()
            if (m := p.search(line))}


@then(parsers.parse('{readers} read owner "{owner}", version "{version}" and commit "{commit}"'))
def read_as(read, readers, owner, version, commit):
    for reader in readers.split(", "):
        want = (owner, version, None if commit == "none" else commit)
        assert read.get(reader) == want, (reader, read)


@then(parsers.parse('every job in "{name}" but release is named "ci", and passed "ci / passed"'))
def names(repo, name):
    for job, spec in jobs(repo, name).items():
        if job == "release":
            assert "name" not in spec
        else:
            assert spec["name"] == ("ci / passed" if job == "passed" else "ci"), job


@when(parsers.parse('I read the job table for "{language}"'), target_fixture="table")
def table(language):
    return ci.JOBS[language]


def declared(workflow):
    data = yaml.safe_load((WORKFLOWS / workflow).read_text())
    return data, set((triggers(data)["workflow_call"] or {}).get("inputs") or {})


@then("every job's inputs are the ones its workflow declares")
def inputs_match(table):
    for name, job in table.items():
        data, inputs = declared(job.workflow)
        passed = set(job.inputs) | set(job.fixed)
        # Every input but those pubkit gives itself is the repository's.
        assert set(job.fixed) <= set(job.internal), name
        assert set(job.inputs) == inputs - set(job.internal), (name, inputs)
        required = {i for i, spec in triggers(data)["workflow_call"]["inputs"].items()
                    if spec.get("required")} if inputs else set()
        assert required <= passed | ({job.when} if job.when else set()), (name, required)
        if job.when:
            assert job.when in job.inputs


@then("every job's workflow is pinned and well-formed")
def well_formed(table):
    for job in table.values():
        data, _ = declared(job.workflow)
        assert len(data["jobs"]) == 1, job.workflow
        for spec in data["jobs"].values():
            assert "if" not in spec, f"{job.workflow} would show as skipped"
        text = (WORKFLOWS / job.workflow).read_text()
        for line in text.splitlines():
            if "uses:" in line and "@" in line:
                assert re.search(r"@[0-9a-f]{40} # v\d", line), line


@then(parsers.parse('"{workflow}" runs the steps of the "{name}" job in "{shared}"'))
def same_steps(workflow, name, shared):
    own = yaml.safe_load((WORKFLOWS / workflow).read_text())["jobs"][name]
    theirs = yaml.safe_load((WORKFLOWS / shared).read_text())["jobs"][name]
    assert own["steps"] == theirs["steps"]
    assert own["runs-on"] == theirs["runs-on"]


@then(parsers.parse('no written workflow passes "{text}"'))
def no_inherit(repo, text):
    written = list((repo / ".github" / "workflows").glob("*.yml"))
    assert written
    for path in written:
        assert text not in path.read_text(), path.name


@then(parsers.parse('every job "{names}" in "{name}" passes SONAR_TOKEN by name'))
def token_by_name(repo, names, name):
    found = jobs(repo, name)
    for job in names.split(", "):
        assert found[job]["secrets"] == {"SONAR_TOKEN": "${{ secrets.SONAR_TOKEN }}"}, job


@then(parsers.parse('stderr does not mention "{text}"'))
def stderr_lacks(result, text):
    assert text not in result.stderr, result.stderr


@then(parsers.parse('"{path}" is not there'))
def not_there(repo, path):
    assert not (repo / path).exists()


# tests/install-lock and the launcher's wheel mirror.

PUBKIT_HASH = "a" * 64
PUBKIT_WHEEL = f"pubkit-{__version__}-py3-none-any.whl"
PUBKIT_URL = f"https://github.com/Sustenaut/pubkit/releases/download/v{__version__}/{PUBKIT_WHEEL}"
PYTEST_LINES = f"pytest==9.0.3 \\\n    --hash=sha256:{'b' * 64} \\\n    --hash=sha256:{'c' * 64}\n"


@pytest.fixture
def mirror():
    return ""


@pytest.fixture
def pypi():
    return ""


def _config(mirror, pypi=""):
    block = {**({"wheels": mirror} if mirror else {}), **({"pypi": pypi} if pypi else {})}
    return {"kind": "utility", "mirror": block} if block else {"kind": "utility"}


@given(parsers.parse('pubkit.json\'s PyPI mirror is "{url}"'), target_fixture="pypi")
def pypi_mirror(url):
    return url


@given(parsers.parse('pubkit.json\'s wheel mirror is "{url}"'), target_fixture="mirror")
def wheel_mirror(url):
    return url


@given('a lock that pins "pubkit" by a GitHub release URL and "pytest" from PyPI', target_fixture="lock")
def lock_file(tmp_path):
    lock = tmp_path / "requirements.lock"
    lock.write_text(f"pubkit @ {PUBKIT_URL} \\\n    --hash=sha256:{PUBKIT_HASH}\n{PYTEST_LINES}")
    return lock


@when(parsers.re(r"tests/install-lock runs with a pip that fails every install "
                 r"(?P<how>of a GitHub release URL|without an index of its own)"), target_fixture="installed")
def run_install_lock(tmp_path, lock, mirror, pypi, how):
    script = tmp_path / "install-lock"
    script.write_text(cli.render(cli.templates("utility")["tests/install-lock"], cli.values(_config(mirror, pypi))))
    seen = tmp_path / "seen"
    seen.mkdir()
    pip = tmp_path / "pip"
    # Keeps each lock it is given (after -r) and its arguments, and fails while
    # the lock names a GitHub release, or while it has no --index-url.
    fails = '! grep -q "https://github.com/" "$lock"' if how.startswith("of") else '[[ " $* " == *" --index-url "* ]]'
    pip.write_text('#!/bin/bash\nn=$(ls "$SEEN" | grep -vc args)\nlock=""; prev=""\n'
                   'for a in "$@"; do [ "$prev" = -r ] && lock=$a; prev=$a; done\n'
                   'cp "$lock" "$SEEN/$n"; echo "$*" > "$SEEN/$n.args"\n' + fails + "\n")
    pip.chmod(0o755)
    done = subprocess.run(["bash", str(script), str(pip), str(lock)], capture_output=True, text=True, check=False,
                          env={**os.environ, "SEEN": str(seen), "INSTALL_LOCK_PAUSE": "0"})
    runs = len([p for p in seen.iterdir() if not p.name.endswith(".args")])
    locks = [(seen / str(n)).read_text() for n in range(runs)]
    args = [(seen / f"{n}.args").read_text().split() for n in range(runs)]
    return {"code": done.returncode, "locks": locks, "args": args, "stderr": done.stderr}


@then(parsers.parse('the last install took its packages from "{index}"'))
def from_index(installed, index):
    last = installed["args"][-1]
    assert last[last.index("--index-url") + 1] == index, installed
    assert all("--index-url" not in a for a in installed["args"][:-1]), "the first three tries use PyPI"


@then(parsers.re(r"pip was run (?P<times>\d) times, and the install (?P<how>passed|failed)"))
def pip_ran(installed, times, how):
    assert len(installed["locks"]) == int(times), installed
    assert (installed["code"] == 0) == (how == "passed"), installed


@then('the last lock pinned "pubkit" at the mirror\'s address for its sha256, with its hash')
def mirrored_pubkit(installed, mirror):
    assert installed["locks"][-1].startswith(
        f"pubkit @ {mirror}/f/{PUBKIT_HASH}/{PUBKIT_WHEEL} \\\n    --hash=sha256:{PUBKIT_HASH}\n")


@then('the last lock left "pytest" as it was')
def pytest_kept(installed):
    assert installed["locks"][-1].endswith(PYTEST_LINES)


@given(parsers.parse("pubkit.json's mirror setting '{setting}'"), target_fixture="setting")
def mirror_setting(setting):
    return json.loads(setting)


@then("sync stops, saying what a mirror setting is")
def mirror_refused(setting):
    with pytest.raises(SystemExit) as stop:
        cli.values({"kind": "utility", "mirror": setting})
    assert 'mirror is {"wheels": "https://host/path", "terraform": "https://host/path", "pypi": "https://host/path"}' \
        in str(stop.value)


@given("a lock that pins this pubkit's wheel", target_fixture="repo_dir")
def pinned_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    digest = hashlib.sha256(b"the wheel").hexdigest()
    (repo / "tests" / "requirements.lock").write_text(
        f"pubkit @ {PUBKIT_URL} \\\n    --hash=sha256:{digest}\n{PYTEST_LINES}")
    return repo


@when(parsers.parse('the launcher fetches pubkit with GitHub failing and the mirror giving "{content}"'),
      target_fixture="launched")
def launch(tmp_path, repo_dir, mirror, content):
    launcher = repo_dir / "pubkit"
    launcher.write_text(cli.render(cli.templates("utility")["pubkit"], cli.values(_config(mirror))))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "curl").write_text(
        '#!/bin/bash\nout=""; url=""\nwhile [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift 2;; -*) shift;; '
        '*) url=$1; shift;; esac; done\necho "$url" >> "$ASKED"\n'
        'case "$url" in https://github.com/*) exit 22;; esac\nprintf %s "$GIVES" > "$out"\n')
    (bin_dir / "uvx").write_text('#!/bin/bash\necho "$@"\n')
    for tool in ("curl", "uvx"):
        (bin_dir / tool).chmod(0o755)
    cache = tmp_path / f"cache-{content.replace(' ', '-')}"
    asked = tmp_path / "asked"
    done = subprocess.run(["bash", str(launcher), "--version"], capture_output=True, text=True, check=False,
                          env={"PATH": f"{bin_dir}{os.pathsep}/usr/bin:/bin", "XDG_CACHE_HOME": str(cache),
                               "GIVES": content, "ASKED": str(asked)})
    return {"code": done.returncode, "stdout": done.stdout, "stderr": done.stderr, "cache": cache,
            "asked": asked.read_text().split()}


@then("uvx is given the cached wheel")
def uvx_cached(launched, mirror):
    digest = hashlib.sha256(b"the wheel").hexdigest()
    held = launched["cache"] / "pubkit" / "wheels" / digest / PUBKIT_WHEEL
    assert launched["code"] == 0 and launched["stdout"].split() == ["--from", str(held), "pubkit", "--version"], launched
    assert launched["asked"] == [PUBKIT_URL, f"{mirror}/f/{digest}/{PUBKIT_WHEEL}"]


@then(parsers.parse('the launcher stops with "{message}"'))
def launcher_stops(launched, message):
    assert launched["code"] == 1 and message in launched["stderr"], launched


@then("the launcher without a mirror runs uvx on pubkit's GitHub release URL, with no fetch")
def launcher_plain():
    text = cli.render(cli.templates("utility")["pubkit"], cli.values({"kind": "utility"}))
    assert f'exec uvx --from "{PUBKIT_URL}" pubkit "$@"' in text and "fetch()" not in text


def _steps(workflow):
    data = yaml.safe_load((WORKFLOWS / workflow).read_text())
    return [s for job in data["jobs"].values() for s in job.get("steps", [])]


# pubkit's own actions, as its reusable workflows pin them: by a release's commit.
SELF_PIN = re.compile(r"^Sustenaut/pubkit/actions/(?P<name>[\w-]+)@(?P<commit>[0-9a-f]{40})$")


def _self_pinned(step, name):
    m = SELF_PIN.match(step.get("uses", ""))
    assert m and m["name"] == name, step
    return m["commit"]


def _action_inputs(name):
    return yaml.safe_load((WORKFLOWS.parents[1] / "actions" / name / "action.yml").read_text())["inputs"]


@then(parsers.parse("every setup step of \"{workflow}\" runs its setup input through actions/retry at pubkit's "
                    "own pin, three times with 15 seconds between"))
def setup_retried(workflow):
    steps = [s for s in _steps(workflow) if s.get("name", "").startswith("Set up, tried three times")]
    assert steps, workflow
    retry = _action_inputs("retry")
    assert (retry["what"]["default"], retry["tries"]["default"], retry["pause"]["default"]) == ("setup", "3", "15")
    for step in steps:
        _self_pinned(step, "retry")
        assert step["with"] == {"run": "${{ inputs.setup }}"}, step
        assert "run" not in step and "env" not in step, step


@then(parsers.parse("the \"{workflow}\" init step runs actions/terraform-init at pubkit's own pin in the "
                    "terraform-dir input, with \"{args}\" and the action's default pause"))
def init_by_action(workflow, args):
    (step,) = [s for s in _steps(workflow) if s.get("name", "").startswith("terraform init, tried three times")]
    _self_pinned(step, "terraform-init")
    init = _action_inputs("terraform-init")
    assert (init["tries"]["default"], init["pause"]["default"]) == ("3", "15")
    assert step["with"] == {"dir": "${{ inputs.terraform-dir }}", "args": args}, step
    assert "run" not in step and "env" not in step, step


@then("every use of pubkit's actions in pubkit's workflows pins one commit, at a release no later than this pubkit")
def self_pins_released():
    """No self-pin at all passes too: the workflows may carry their own steps."""
    pins = set()
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text()
        uses = [u for u in refs.PUBKIT_USE.findall(text) if "/pubkit/actions/" in u]
        pinned = [m for m in refs.PUBKIT_PIN.finditer(text) if m["name"].startswith("actions/")]
        assert len(pinned) == len(uses) and all(m["commit"] for m in pinned), \
            f"{path.name}: pin pubkit's actions by a release's commit, with its version: {uses}"
        pins |= {(m["commit"], m["version"]) for m in pinned}
    assert len(pins) <= 1, f"pubkit's workflows pin its actions at more than one commit: {sorted(pins)}"
    for _, version in pins:
        assert tuple(map(int, version.split("."))) <= tuple(map(int, __version__.split("."))), \
            f"pubkit's workflows pin its actions at v{version}, later than this pubkit ({__version__})"


@then("no reusable workflow of pubkit's uses an action by a local path")
def no_local_actions():
    reusable = 0
    for path in sorted(WORKFLOWS.glob("*.yml")):
        data = yaml.safe_load(path.read_text())
        on = data.get("on", data.get(True))  # YAML 1.1 reads a bare `on` as true
        if "workflow_call" in (on if isinstance(on, (dict, list)) else [on]):
            reusable += 1
            assert not [s for s in _steps(path.name) if s.get("uses", "").startswith("./")], path.name
    assert reusable, "no reusable workflow found"


@given(parsers.parse("pubkit.json's protect block is {block}"))
def protect_block(repo, block):
    path = repo / "pubkit.json"
    config = json.loads(path.read_text())
    config["protect"] = json.loads(block)
    path.write_text(json.dumps(config))


@given(parsers.parse('the workflow "{name}" has the job "{job}" on "{events}"'))
def plain_job(repo, name, job, events):
    path = repo / ".github" / "workflows" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    on = ", ".join(e.strip() for e in events.split(","))
    path.write_text(f"on: [{on}]\njobs:\n  passed:\n    name: {job}\n    runs-on: ubuntu-latest\n"
                    "    steps:\n      - run: true\n")


@given(parsers.parse('the workflow "{name}" calls "{uses}" as "{job}" on "{events}"'))
def calling_job(repo, name, uses, job, events):
    path = repo / ".github" / "workflows" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    on = ", ".join(e.strip() for e in events.split(","))
    path.write_text(f"on: [{on}]\njobs:\n  {job}:\n    uses: {uses}\n")


PUBKIT_ACTIONS = WORKFLOWS.parents[1] / "actions"


def _all_steps(data):
    for job in (data.get("jobs") or {}).values():
        yield from job.get("steps") or []
    yield from ((data.get("runs") or {}).get("steps") or [])


@then("every upload-artifact and actions/cache step in pubkit's workflows continues on error, within five minutes")
def uploads_non_fatal():
    found = 0
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for step in _all_steps(yaml.safe_load(path.read_text())):
            if re.match(r"actions/(upload-artifact|cache)@", step.get("uses", "")):
                found += 1
                assert step.get("continue-on-error") is True, (path.name, step)
                assert step.get("timeout-minutes") == 5, (path.name, step)
    assert found


@then("every cache restore and save in pubkit's cache action continues on error")
def cache_action_non_fatal():
    steps = [s for s in _all_steps(yaml.safe_load((PUBKIT_ACTIONS / "cache" / "action.yml").read_text()))
             if re.match(r"actions/cache/(restore|save)@", s.get("uses", ""))]
    assert len(steps) == 16
    assert all(s.get("continue-on-error") is True for s in steps)


@then("no step that runs a test command continues on error")
def tests_fatal():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for step in _all_steps(yaml.safe_load(path.read_text())):
            run = step.get("run", "")
            if "inputs.test" in run or "inputs.browser-test" in run or "make check" in run:
                assert not step.get("continue-on-error"), (path.name, step)
