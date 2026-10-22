"""The CI `pubkit sync` writes from the `ci` block of pubkit.json.

A reusable workflow that serves every repository has to skip the jobs a
repository does not use, and GitHub shows each as skipped. Here pubkit
writes the repository's workflows instead, one per event, each calling
only the jobs that event runs: one reusable workflow per job
(job-*.yml), at this pubkit's tag.

    "ci": {
      "language": "node",
      "events": ["pr", "main", "nightly"],
      "cron": "17 3 * * *",
      "with": {"browser-test": "npm run test:browser", "coverage-report": "coverage/"},
      "jobs": {"review": false, "sonar": {"events": ["pr", "main"]}},
      "release": {"pubrel": "0.5.4", "with": {}}
    }

`language` is node or python; without it, a package.json with no
pyproject.toml beside it makes it node. `events` are the workflows written:
pr.yml (pull requests), main.yml (pushes to main) and nightly.yml (the
schedule `cron`, and by hand); pr and main by default. `with` gives the
jobs' inputs: each goes to every job that takes it, and an input no job
takes stops sync. A job runs on the events JOBS gives it unless `jobs`
narrows them, and `false` drops it, as a private repository drops review.
The browser job runs only when `with` gives a browser-test. `release`
adds pubrel's release workflow to pr.yml and main.yml, at this pubkit's
pubrel unless it names one; `false` leaves it out.

Each workflow ends in `passed` (the check a ruleset requires, `ci /
passed`), which passes only when every job it needs succeeded. Every job
is named `ci`, so its checks read `ci / test`, `ci / sonar` and so on.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__

EVENTS = ("pr", "main", "nightly")
DEFAULT_EVENTS = ("pr", "main")
CRON = "17 3 * * *"
TRIGGERS = {
    "pr": "  pull_request:\n",
    "main": "  push: { branches: [main] }\n",
    "nightly": "  schedule: [{ cron: 'CRON' }]\n  workflow_dispatch:\n",
}
# The events the release workflow runs on: a pull request records and
# checks its release; a push to main tags and releases it.
RELEASE_EVENTS = ("pr", "main")
SETTINGS = ("language", "events", "cron", "with", "jobs", "release")
HEADER = ("# Written by `pubkit sync` from the ci block of pubkit.json. Do not edit:\n"
          "# change pubkit.json and run `pubkit sync`.\n")


@dataclass(frozen=True)
class Job:
    """One job: the reusable workflow it calls, the inputs it takes, and
    the jobs whose results it waits for, where they run."""
    workflow: str
    inputs: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()
    events: tuple[str, ...] = EVENTS
    when: str | None = None  # the input it runs only when given
    secrets: bool = False
    fixed: dict[str, str] = field(default_factory=dict)  # inputs pubkit gives
    # Inputs that are pubkit's to give, whether or not this language gives
    # them, never the repository's.
    internal: tuple[str, ...] = ()


NODE_TEST = ("node-version", "python-version", "apt-packages", "setup", "test", "sync-check",
             "coverage-report", "fuzz-runs", "fuzz-runs-scheduled")
PYTHON_TEST = ("python-version", "apt-packages", "setup", "test", "sync-check",
               "coverage-report", "fuzz-runs", "fuzz-runs-scheduled")
SONAR_INTERNAL = ("report-property", "report-pattern")
JOBS: dict[str, dict[str, Job]] = {
    "node": {
        "test": Job("job-node-test.yml", NODE_TEST),
        "browser": Job("job-node-browser.yml",
                       ("node-version", "apt-packages", "setup", "browser-setup", "browser-test",
                        "browser-coverage-report"), when="browser-test"),
        "audit": Job("job-node-audit.yml", ("node-version",)),
        "osv": Job("job-osv.yml"),
        "review": Job("job-review.yml", events=("pr",)),
        "sonar": Job("job-sonar.yml", ("sonar-args",), needs=("test", "browser"), secrets=True,
                     fixed={"report-property": "sonar.javascript.lcov.reportPaths"},
                     internal=SONAR_INTERNAL),
    },
    "python": {
        "functional": Job("job-python-functional.yml", PYTHON_TEST),
        "browser": Job("job-python-browser.yml",
                       ("python-version", "apt-packages", "setup", "browser-setup", "browser-test",
                        "browser-coverage-report"), when="browser-test"),
        "audit": Job("job-python-audit.yml", ("python-version", "audit-requirements")),
        "osv": Job("job-osv.yml"),
        "review": Job("job-review.yml", events=("pr",)),
        "sonar": Job("job-sonar.yml", ("sonar-args",), needs=("functional", "browser"), secrets=True,
                     fixed={"report-property": "sonar.python.coverage.reportPaths",
                            "report-pattern": "*.xml"},
                     internal=SONAR_INTERNAL),
    },
}


def stop(text: str) -> None:
    sys.exit(f"pubkit: pubkit.json's ci {text}")


def is_node(root: Path) -> bool:
    """A Node package: a package.json, and no pyproject.toml beside it."""
    return (root / "package.json").exists() and not (root / "pyproject.toml").exists()


def settings(config: dict[str, Any], root: Path) -> dict[str, Any] | None:
    """The ci block, checked and filled in, or None when pubkit.json has
    none and the repository writes its own workflows."""
    given = config.get("ci")
    if given is None:
        return None
    if config.get("kind") != "utility":
        stop("block is for a utility repository so far; a rust or service one keeps its ci.yml")
    if not isinstance(given, dict):
        stop('block must be an object, such as {"with": {"test": "npm test"}}')
    unknown = sorted(set(given) - set(SETTINGS))
    if unknown:
        stop(f"block has no setting {', '.join(unknown)}; it takes {', '.join(SETTINGS)}")
    language = given.get("language", "node" if is_node(root) else "python")
    if language not in JOBS:
        stop(f"language must be one of {', '.join(JOBS)}, not {language!r}")
    table = JOBS[language]
    events = given.get("events", list(DEFAULT_EVENTS))
    if not isinstance(events, list) or not events or not set(events) <= set(EVENTS):
        stop(f"events must list some of {', '.join(EVENTS)}")
    cron = given.get("cron", CRON)
    if not isinstance(cron, str) or len(cron.split()) != 5:
        stop(f"cron must be a schedule of five fields, such as {CRON!r}")
    inputs = given.get("with", {})
    if not isinstance(inputs, dict) or not all(
            isinstance(v, (str, bool, int, float)) for v in inputs.values()):
        stop("with must be an object of the jobs' inputs: strings, numbers or true/false")
    taken = {i for job in table.values() for i in job.inputs}
    unknown = sorted(set(inputs) - taken)
    if unknown:
        stop(f"with gives {', '.join(unknown)}, which no {language} job takes; "
             f"they take {', '.join(sorted(taken))}")
    overrides = given.get("jobs", {})
    if not isinstance(overrides, dict):
        stop('jobs must be an object, such as {"review": false}')
    jobs: dict[str, tuple[str, ...]] = {}
    for name, job in table.items():
        override = overrides.get(name, {})
        if override is False:
            continue
        if not isinstance(override, dict) or set(override) - {"events"}:
            stop(f'jobs.{name} must be false or {{"events": [...]}}')
        on = override.get("events", list(job.events))
        if not isinstance(on, list) or not set(on) <= set(EVENTS):
            stop(f"jobs.{name}.events must list some of {', '.join(EVENTS)}")
        if job.when and inputs.get(job.when, "") == "":
            continue
        jobs[name] = tuple(e for e in EVENTS if e in on and e in events)
    unknown = sorted(set(overrides) - set(table))
    if unknown:
        stop(f"jobs names {', '.join(unknown)}, which {language} has not; it has {', '.join(table)}")
    from .new import PUBREL_PIN  # noqa: PLC0415
    release = given.get("release", True)
    if release is True:
        release = {}
    if release is not False:
        if not isinstance(release, dict) or set(release) - {"pubrel", "with"}:
            stop('release must be true, false or {"pubrel": "0.5.4", "with": {...}}')
        pubrel = release.get("pubrel", PUBREL_PIN)
        extra = release.get("with", {})
        if not isinstance(pubrel, str) or not pubrel[:1].isdigit():
            stop('release.pubrel must be a version, such as "0.5.4"')
        if not isinstance(extra, dict):
            stop("release.with must be an object of the release workflow's inputs")
        release = {"pubrel": pubrel, "with": extra}
    return {"language": language, "events": [e for e in EVENTS if e in events], "cron": cron,
            "with": inputs, "jobs": jobs, "release": release}


def flow(values: dict[str, Any]) -> str:
    """A `with:` mapping on one line: JSON, which YAML reads as it is."""
    return json.dumps(values, ensure_ascii=False)


def workflow(ci: dict[str, Any], event: str) -> str:
    """The text of one event's workflow."""
    table = JOBS[ci["language"]]
    names = [name for name, on in ci["jobs"].items() if event in on]
    out = [HEADER, f"name: {event}\n\non:\n", TRIGGERS[event].replace("CRON", ci["cron"]),
           "\npermissions:\n  contents: read\n\njobs:\n"]
    for name in names:
        job = table[name]
        given = {**{i: ci["with"][i] for i in job.inputs if i in ci["with"]}, **job.fixed}
        needs = [n for n in job.needs if n in names]
        out.append(f"{'' if name == names[0] else chr(10)}  {name}:\n    name: ci\n")
        if needs:
            out.append(f"    needs: [{', '.join(needs)}]\n")
        out.append(f"    uses: schryer/pubkit/.github/workflows/{job.workflow}@v{__version__}\n")
        if given:
            out.append(f"    with: {flow(given)}\n")
        if job.secrets:
            out.append("    secrets: inherit\n")
    out.append(
        "\n  # The one check a ruleset requires: it passes only when every job\n"
        "  # above succeeded. None of them is ever skipped for not being asked for.\n"
        "  passed:\n    name: ci / passed\n    if: always()\n"
        f"    needs: [{', '.join(names)}]\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - run: |\n          echo \"$RESULTS\"\n"
        "          echo \"$RESULTS\" | jq -e 'all(.[]; .result == \"success\")' > /dev/null\n"
        "        env: { RESULTS: '${{ toJSON(needs) }}' }\n")
    release = ci["release"]
    if release and event in RELEASE_EVENTS:
        pubrel = release["pubrel"]
        out.append(
            "\n  # pubrel's release workflow: on a pull request, a code change records\n"
            "  # what it changes and a release is the bump its changes require; on\n"
            "  # main, a published version is tagged and released.\n"
            "  release:\n    needs: passed\n"
            f"    uses: schryer/pubrel/.github/workflows/release.yml@v{pubrel}\n"
            f"    with: {flow({'pubrel-version': f'v{pubrel}', **release['with']})}\n"
            "    permissions:\n      contents: write\n")
    return "".join(out)


def path(event: str) -> str:
    return f".github/workflows/{event}.yml"


def generated(config: dict[str, Any], root: Path) -> list[tuple[str, str]]:
    """Each workflow pubkit writes, by path: none without a ci block."""
    ci = settings(config, root)
    if ci is None:
        return []
    return [(path(event), workflow(ci, event)) for event in ci["events"]]


def is_generated(text: str) -> bool:
    return text.startswith(HEADER)


def leftovers(config: dict[str, Any], root: Path) -> list[str]:
    """Workflows the generated ones replace: a hand-written ci.yml, and a
    generated one for an event the ci block no longer runs."""
    ci = settings(config, root)
    if ci is None:
        return []
    out = []
    old = root / ".github" / "workflows" / "ci.yml"
    if old.exists():
        out.append(".github/workflows/ci.yml")
    for event in EVENTS:
        target = root / path(event)
        if event not in ci["events"] and target.exists() and is_generated(
                target.read_text(encoding="utf-8")):
            out.append(path(event))
    return out
