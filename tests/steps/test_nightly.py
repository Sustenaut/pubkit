"""Steps for the nightly: `pubkit pins`, the parts of `pubkit crates` and
`pubkit nightly` that need no network, the workflow and its script, and
`pubkit new` without a key."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import yaml
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

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
    assert '"$pins:$branch"' in script and '"${commits[0]}" = pins' in script
    assert script.index('wait_ci "$head"') < script.index('wait_ci "$pins"')


def _steps(workflow):
    return workflow["data"]["jobs"]["update"]["steps"]


@then("each repository's token comes from the Sustenaut nightly app, for that repository alone")
def app_token(workflow):
    app = next(s for s in _steps(workflow) if s.get("id") == "app")
    assert re.fullmatch(r"actions/create-github-app-token@[0-9a-f]{40}", app["uses"])
    assert app["if"] == "vars.NIGHTLY_APP_ID != ''"
    given = app["with"]
    assert given["app-id"] == "${{ vars.NIGHTLY_APP_ID }}"
    assert given["private-key"] == "${{ secrets.NIGHTLY_APP_KEY }}"
    assert given["owner"] == "${{ steps.where.outputs.owner }}"
    assert given["repositories"] == "${{ steps.where.outputs.name }}"
    update = next(s for s in _steps(workflow) if "update.sh" in s.get("run", ""))
    assert update["env"]["GH_TOKEN"] == "${{ steps.token.outputs.token }}"


@then("the token may write only contents, pull requests and workflows, and read actions")
def app_permissions(workflow):
    given = next(s for s in _steps(workflow) if s.get("id") == "app")["with"]
    asked = {k: v for k, v in given.items() if k.startswith("permission-")}
    assert asked == {"permission-contents": "write", "permission-pull-requests": "write",
                     "permission-workflows": "write", "permission-actions": "read"}


@then("without the app it falls back to PUBKIT_BUMP_TOKEN with a warning")
def fallback(workflow):
    token = next(s for s in _steps(workflow) if s.get("id") == "token")
    assert token["env"]["BUMP_TOKEN"] == "${{ secrets.PUBKIT_BUMP_TOKEN }}"
    assert "::warning::No NIGHTLY_APP_ID" in token["run"]
    assert token["run"].index("APP_TOKEN") < token["run"].index("BUMP_TOKEN")
    assert "GH_TOKEN" not in workflow["data"]["jobs"]["update"].get("env", {})


@when(parsers.parse("the repository's pubkit.json reads '{text}'"))
def pubkit_json(repo, text):
    (repo / "pubkit.json").write_text(text)


@then(parsers.parse('the settings are update "{update}" and fuzz "{fuzz}"'))
def settings_are(repo, update, fuzz):
    on = lambda switches: [lang for lang, value in switches.items() if value]  # noqa: E731
    got = nightly.settings(repo)
    assert on(got["update"]) == [x for x in update.split(", ") if x != "none"]
    assert on(got["fuzz"]) == [x for x in fuzz.split(", ") if x != "none"]


@then(parsers.parse('reading the nightly settings stops with "{message}"'))
def settings_stop(repo, message):
    with pytest.raises(SystemExit) as stop:
        nightly.settings(repo)
    assert message in str(stop.value)


@given(parsers.parse('a package-lock.json where "{name}" is {version} and "{indirect}" is {other}'))
def package_lock(repo, name, version, indirect, other):
    (repo / "package-lock.json").write_text(json.dumps({"packages": {
        "": {"dependencies": {name: "^" + version}},
        f"node_modules/{name}": {"version": version},
        f"node_modules/{indirect}": {"version": other}}}))


@then(parsers.parse('the npm packages listed are "{expected}"'))
def npm_listed(repo, expected):
    assert nightly.npm_locked(repo) == dict(p.split("@") for p in expected.split(", "))


@then("the npm and Python updates are empty")
def nothing_to_update(repo):
    assert nightly.npm(repo) == [] and nightly.npm_locked(repo) == {}
    assert nightly.python(repo) == [] and nightly.python_locked(repo) == {}


@then(parsers.parse('the packages that moved from "{old}" to "{new}" are "{expected}"'))
def moved(old, new, expected):
    parse = lambda text: dict(p.split("@") for p in text.split(", "))  # noqa: E731
    assert nightly.changed(parse(old), parse(new)) == expected.split(", ")


@when("the nightly writes a body for a repository that takes no updates", target_fixture="body")
def body_no_updates():
    return nightly.body(["pubkit 0.8.0 -> 0.9.0"], [], [], {"npm": ["marked 18.1.0 -> 18.2.0"]},
                        update=False)


@then(parsers.parse('the body says "{text}" and lists no "{other}"'))
def body_says(body, text, other):
    assert text in body and other not in body


@when("the nightly writes a body with npm updates", target_fixture="body")
def body_npm():
    return nightly.body([], [], [], {"npm packages": ["marked 18.1.0 -> 18.2.0"],
                                     "Python packages": []})


@then(parsers.parse('the body has the section "{heading}" with "{line}" and no "{absent}"'))
def body_section(body, heading, line, absent):
    assert heading in body and f"- {line}" in body and absent not in body


LOCK_TEXT = "pytest==9.0.0 \\\n    --hash=sha256:00\n"


@given("a git repository with a package-lock.json and a Python lock")
def git_repository(repo):
    (repo / "tests").mkdir()
    (repo / "tests" / "requirements.txt").write_text("pytest\n")
    (repo / "tests" / "requirements.lock").write_text(LOCK_TEXT)
    package_lock(repo, "marked", "18.1.0", "entities", "4.5.0")
    for argv in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "start"]):
        subprocess.run(["git", *argv], cwd=repo, check=True)


@given("its pubkit.json turns every language's updates off")
def updates_off(repo):
    (repo / "pubkit.json").write_text('{"update": false}')
    subprocess.run(["git", "add", "pubkit.json"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "off"],
                   cwd=repo, check=True)


@when(parsers.parse('the nightly runs with npm moving "{name}" to {version} and the lock moving '
                    '"{package}" to {new}'), target_fixture="ran")
def nightly_runs(repo, monkeypatch, capsys, name, version, package, new):
    from pubkit import cli  # noqa: PLC0415

    real = subprocess.run

    def fake(argv, *args, **kwargs):
        if argv[0] == "npm":
            package_lock(repo, name, version, "entities", "4.6.0")
            return subprocess.CompletedProcess(argv, 0, "", "")
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(nightly.subprocess, "run", fake)
    monkeypatch.setattr(nightly, "pins", lambda *a, **k: [])
    monkeypatch.setattr(nightly, "record", lambda *a, **k: [])
    monkeypatch.setattr(cli, "lock", lambda root: (root / cli.LOCK).write_text(
        LOCK_TEXT.replace("9.0.0", new)))
    monkeypatch.chdir(repo)
    assert nightly.nightly(repo, "0.5.4", "0.2.1", Path("body.md")) == 0
    return {"made": capsys.readouterr().out.strip(), "body": (repo / "body.md").read_text()}


@then(parsers.parse('it made the commits "{made}"'))
def made_commits(ran, made):
    assert ran["made"] == made


@then("it made no commits")
def made_none(ran):
    assert ran["made"] == ""


@then(parsers.parse('the body lists "{first}" and "{second}"'))
def body_lists(ran, first, second):
    assert f"- {first}" in ran["body"] and f"- {second}" in ran["body"]


@then(parsers.parse('its body says "{text}" and lists no "{other}"'))
def ran_body_says(ran, text, other):
    assert text in ran["body"] and other not in ran["body"]


@given(parsers.parse('Cargo.lock locks "{names}"'))
def cargo_lock(repo, names):
    (repo / "Cargo.lock").write_text("".join(
        f'[[package]]\nname = "{name}"\nversion = "1.0.0"\n\n' for name in names.split(", ")))


@given("cargo stands in, recording what it is asked")
def fake_cargo(tmp_path, env):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cargo = bin_dir / "cargo"
    cargo.write_text('#!/bin/sh\necho "$*" >> "$CARGO_ASKED"\n')
    cargo.chmod(0o755)
    env["CARGO_ASKED"] = str(tmp_path / "cargo-asked")
    env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


def _updated(tmp_path):
    asked = tmp_path / "cargo-asked"
    return re.findall(r"-p (\S+)", asked.read_text()) if asked.exists() else []


@then(parsers.parse('cargo was asked to update "{crate}" alone'))
def updated_alone(tmp_path, crate):
    assert _updated(tmp_path) == [crate]


@then("cargo was asked to update nothing")
def updated_nothing(tmp_path):
    assert _updated(tmp_path) == []


@then(parsers.parse('stderr does not mention "{text}"'))
def no_mention(result, text):
    err = result.stderr.decode() if isinstance(result.stderr, bytes) else result.stderr
    assert text not in err, err


@when(parsers.parse('cargo update --dry-run here says "{a}" and "{b}"'), target_fixture="tried")
def dry_run_here(repo, monkeypatch, a, b):
    said = subprocess.CompletedProcess([], 0, "", f"    {a}\n    {b}\n")
    monkeypatch.setattr(nightly, "cargo", lambda *_: said)
    return nightly.candidates(repo)


@given(parsers.parse('the origin remote is "{url}"'))
def origin_remote(repo, url):
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", url], check=True)


@when(parsers.parse('I start the package "{name}" owned by "{owner}"'), target_fixture="printed")
def start_owned(repo, monkeypatch, capsys, name, owner):
    monkeypatch.setattr(new, "check_tools", lambda: [])
    assert new.run(repo, name, f"PKG-{name.upper():<6.6}-10-2026".replace(" ", "X"), "",
                   owner=owner, key=False, lock=False) == 0
    return capsys.readouterr().out


@then(parsers.parse('what it printed says "{first}" and "{second}"'))
def printed_says(printed, first, second):
    flat = " ".join(printed.split())
    assert first in flat and second in flat, printed


@then(parsers.parse('what it printed does not say "{first}" or "{second}"'))
def printed_lacks(printed, first, second):
    assert first not in printed and second not in printed, printed


@then(parsers.parse('the repository has no "{path}"'))
def has_no(repo, path):
    assert not (repo / path).exists()


@when("the tools are checked with no pub installed", target_fixture="problems")
def tools_checked(monkeypatch):
    monkeypatch.setattr(new, "version_of", lambda tool: None)
    return new.check_tools()


@then(parsers.parse('the install line names "{url}"'))
def install_line(problems, url):
    assert any(p.startswith("pub is not installed") and f"--git {url} " in p for p in problems)
    assert new.PUB_GIT == url


@when(parsers.parse('I start the package "{name}" without a key'))
def start(repo, monkeypatch, name):
    monkeypatch.setattr(new, "check_tools", lambda: [])
    assert new.run(repo, name, f"PKG-{name.upper():<6.6}-10-2026".replace(" ", "X"), "",
                   key=False, lock=False) == 0


@then(parsers.parse('it conforms to the standard but for "{rule}"'))
def conforms_but(repo, rule):
    _, problems = conform.run(repo, json.loads((repo / "pubkit.json").read_text()))
    assert problems and all(p.startswith(f"{rule}:") for p in problems), problems
