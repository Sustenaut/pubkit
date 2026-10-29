"""Steps for the CI `pubkit sync` writes from pubkit.json's ci block."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml
from pytest_bdd import given, parsers, scenarios, then, when

from pubkit import __version__, ci

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


@then(parsers.parse('"{name}" has no release'))
def no_release(repo, name):
    assert "release" not in jobs(repo, name)


@then(parsers.parse('no workflow is written for "{event}"'))
def not_written(repo, event):
    assert not (repo / ".github" / "workflows" / f"{event}.yml").exists()


@then('no job in "pr.yml" or "main.yml" has a condition but passed')
def unconditional(repo):
    for name in ("pr.yml", "main.yml", "nightly.yml"):
        if not (repo / ".github" / "workflows" / name).exists():
            continue
        for job, spec in jobs(repo, name).items():
            assert "if" not in spec or job == "passed", f"{name}: {job} has if: {spec.get('if')}"


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


@then(parsers.parse('"{name}" calls pubrel\'s release workflow at "{version}" with "{key}" "{value}"'))
def release(repo, name, version, key, value):
    job = jobs(repo, name)["release"]
    assert job["uses"] == f"schryer/pubrel/.github/workflows/release.yml@v{version}"
    assert job["with"]["pubrel-version"] == f"v{version}"
    assert job["with"][key] == value
    assert job["needs"] == "passed"


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
