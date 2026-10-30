"""Steps for the CI `pubkit sync` writes from pubkit.json's ci block."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import yaml
from pytest_bdd import given, parsers, scenarios, then, when

from pubkit import __version__, ci, refs

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


@then("on a push to main it skips the tests the merge queue already passed on its commit")
def own_shortcut(own):
    jobs_ = own["jobs"]
    tested = jobs_["tested"]
    assert tested["uses"] == "./.github/workflows/job-tested.yml"
    assert tested["if"] == "github.event_name == 'push'"
    assert tested["with"] == {"workflow": ".github/workflows/ci.yml"}
    for job in ("functional", "audit", "osv", "terraform", "deploy-static", "cache-action"):
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
                    "      - uses: Sustenaut/pubkit/actions/deploy-static@v0.17.1\n"
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
    assert re.fullmatch(rf"{refs.OWNER}/pubkit/actions/deploy-static@(v{re.escape(__version__)}|[0-9a-f]{{40}})",
                        action["uses"]), action["uses"]
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
    from pubkit.board import collect  # noqa: PLC0415

    patterns = {"conform": conform.WORKFLOW_PIN, "bump": freshness.WORKFLOW_PIN,
                "nightly": nightly.CI_PIN, "board": collect.PUBKIT_PIN,
                "release": refs.RELEASE_PIN}
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
