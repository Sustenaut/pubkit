"""Steps for the retry and terraform-init actions: each action's step run
as GitHub runs a composite step, with its inputs (or their defaults) in
the environment its `env` gives, a sleep that records how long it was
asked to wait, and a stub terraform that records each init."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import yaml
from pytest_bdd import parsers, scenarios, then, when

scenarios("retry.feature")

ACTIONS = Path(__file__).resolve().parents[2] / "actions"
INPUT = re.compile(r"^\$\{\{ inputs\.([\w-]+) \}\}$")

# Counts its runs in $RUNS; fails the first time, or every time.
FAILS_FIRST = 'echo run >> "$RUNS"; [ "$(wc -l < "$RUNS")" -gt 1 ]'
ALWAYS_FAILS = 'echo run >> "$RUNS"; false'


def _action(name):
    return yaml.safe_load((ACTIONS / name / "action.yml").read_text())


def run_action(work, name, given, extra_env=None):
    """Run the action's one step in `work` with `given` inputs, the rest at
    their defaults, as `bash -e`, with PATH's sleep recording its seconds."""
    action = _action(name)
    inputs = {key: str(spec.get("default", "")) for key, spec in action["inputs"].items()}
    unknown = set(given) - set(inputs)
    assert not unknown, f"{name} has no input {unknown}"
    inputs.update(given)
    (step,) = action["runs"]["steps"]
    env = {key: inputs[INPUT.match(value)[1]] for key, value in step["env"].items()}
    bin_dir = work / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    slept = work / "slept"
    slept.write_text("")
    (bin_dir / "sleep").write_text('#!/bin/sh\necho "$1" >> "$SLEPT"\n')
    (bin_dir / "sleep").chmod(0o755)
    runs = work / "runs"
    done = subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
        capture_output=True, text=True, check=False, cwd=work,
        env={**os.environ, **env, **(extra_env or {}),
             "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
             "GITHUB_ACTION_PATH": str(ACTIONS / name), "RUNNER_TEMP": str(work),
             "SLEPT": str(slept), "RUNS": str(runs)})
    return {"code": done.returncode, "stdout": done.stdout, "stderr": done.stderr, "work": work,
            "runs": len(runs.read_text().splitlines()) if runs.exists() else 0,
            "slept": slept.read_text().split()}


@when(parsers.re(r"the retry action runs a script that (?P<how>fails the first time|always fails)"
                 r'(?:, with what "(?P<what>[^"]*)", tries "(?P<tries>[^"]*)" and pause "(?P<pause>[^"]*)")?$'),
      target_fixture="ran")
def retry_runs(tmp_path, how, what, tries, pause):
    given = {"run": FAILS_FIRST if how == "fails the first time" else ALWAYS_FAILS}
    if tries is not None:
        given.update(what=what, tries=tries, pause=pause)
    return run_action(tmp_path, "retry", given)


@when(parsers.re(r'the retry action runs the script "(?P<script>.*)"$'), target_fixture="ran")
def retry_script(tmp_path, script):
    return run_action(tmp_path, "retry", {"run": script})


@then(parsers.re(r"the script ran (?P<times>\d+) times, and the step (?P<how>passed|failed)(?: with (?P<code>\d+))?$"))
def script_ran(ran, times, how, code):
    assert ran["runs"] == int(times), ran
    assert (ran["code"] == 0) == (how == "passed"), ran
    if code is not None:
        assert ran["code"] == int(code), ran


@then(parsers.re(r'it warned (?P<warnings>".*")$'))
def warned(ran, warnings):
    lines = ran["stdout"].splitlines()
    for warning in re.findall(r'"([^"]*)"', warnings):
        assert f"::warning::{warning}" in lines, ran


@then(parsers.re(r"it slept (?P<times>\d+) times? for (?P<seconds>\d+) seconds"))
def slept(ran, times, seconds):
    assert ran["slept"] == [seconds] * int(times), ran


@then(parsers.parse('it reported the error "{error}"'))
def reported(ran, error):
    assert any(line.startswith("::error::retry: ") and error in line for line in ran["stdout"].splitlines()), ran


@then("no try went past the failing line")
def stopped_at_failure(ran):
    assert not (ran["work"] / "runs.after").exists()


@then(parsers.parse('the script printed "{text}", and no file "{name}" was made'))
def printed_as_given(ran, text, name):
    assert (ran["work"] / "runs.text").read_text() == text
    assert not (ran["work"] / name).exists()


@then(parsers.parse('every step of the "{first}" and "{second}" actions takes its inputs from its environment alone'))
def inputs_by_env(first, second):
    for name in (first, second):
        for step in _action(name)["runs"]["steps"]:
            assert "${{" not in step["run"], f"{name}: {step['run']}"
            assert all(INPUT.match(value) for value in step["env"].values()), f"{name}: {step['env']}"


# The stub terraform: records each run's arguments and the CLI
# configuration it was given (or "-"), and passes as the scenario says.
TERRAFORM = """#!/bin/bash
n=$(ls "$INITS" | wc -l)
printf '%s\\n' "$*" > "$INITS/$n.args"
if [ -n "${TF_CLI_CONFIG_FILE:-}" ]; then cp "$TF_CLI_CONFIG_FILE" "$INITS/$n.config"; fi
case "$HOW" in
  passes) exit 0 ;;
  mirror) [ -n "${TF_CLI_CONFIG_FILE:-}" ] ;;
  *) exit 1 ;;
esac
"""
HOWS = {"passes": "passes", "fails unless given a provider mirror": "mirror", "always fails": "fails"}


@when(parsers.re(r'the terraform-init action runs (?:in "(?P<dir>[^"]*)" with "(?P<args>[^"]*)"|with its defaults '
                 r'but tries "(?P<tries>\d+)" and pause "(?P<pause>\d+)"), with a terraform that '
                 r'(?P<how>passes|fails unless given a provider mirror|always fails), '
                 r'and (?:the mirror "(?P<url>[^"]+)"|no mirror)$'), target_fixture="ran")
def init_runs(tmp_path, dir, args, tries, pause, how, url):
    work = tmp_path / "work"
    inits = work / "inits"
    inits.mkdir(parents=True)
    (work / "pubkit.json").write_text(json.dumps({"kind": "service", **({"mirror": {"terraform": url}} if url else {})}))
    terraform = tmp_path / "terraform"
    terraform.write_text(TERRAFORM)
    terraform.chmod(0o755)
    given = {"terraform": str(terraform)}
    given.update({"dir": dir, "args": args} if dir is not None else {"tries": tries, "pause": pause})
    ran = run_action(work, "terraform-init", given, {"INITS": str(inits), "HOW": HOWS[how]})
    count = len(list(inits.glob("*.args")))
    ran["inits"] = [((inits / f"{n}.args").read_text().strip(),
                     (inits / f"{n}.config").read_text() if (inits / f"{n}.config").exists() else None)
                    for n in range(count)]
    return ran


@then(parsers.re(r"terraform init ran (?P<times>\d+) times, and the step (?P<how>passed|failed)(?: with (?P<code>\d+))?$"))
def init_ran(ran, times, how, code):
    assert len(ran["inits"]) == int(times), ran
    assert (ran["code"] == 0) == (how == "passed"), ran
    if code is not None:
        assert ran["code"] == int(code), ran


@then(parsers.parse('every init ran "{args}"'))
def init_args(ran, args):
    assert ran["inits"] and all(given == args for given, _ in ran["inits"]), ran


@then("no init was given a CLI configuration")
def no_config(ran):
    assert all(config is None for _, config in ran["inits"]), ran


@then(parsers.parse('the last init\'s CLI configuration installs every provider from "{url}"'))
def init_mirrored(ran, url):
    assert [config for _, config in ran["inits"]][:-1] == [None] * (len(ran["inits"]) - 1), ran
    assert ran["inits"][-1][1] == f'provider_installation {{\n  network_mirror {{ url = "{url}" }}\n}}\n'
