"""The CI `pubkit sync` writes from the `ci` block of pubkit.json.

A reusable workflow that serves every repository has to skip the jobs a
repository does not use, and GitHub shows each as skipped. Here pubkit
writes the repository's workflows instead, one per event, each calling
only the jobs that event runs: one reusable workflow per job
(job-*.yml), pinned to the commit of this pubkit's release tag, with the
version in a comment (refs.py).

    "ci": {
      "language": "node",
      "events": ["pr", "main", "nightly"],
      "cron": "17 3 * * *",
      "with": {"browser-test": "npm run test:browser", "coverage-report": "coverage/"},
      "jobs": {"review": false, "sonar": {"events": ["pr", "main"]}},
      "release": {"pubrel": "0.5.4", "with": {}}
    }

`language` is a utility's, node or python; without it, a package.json
with no pyproject.toml beside it makes it node. A rust or service
repository's CI is its kind's. `events` are the workflows written: pr.yml
(pull requests), main.yml (pushes to main) and nightly.yml (the schedule
`cron`, and by hand); pr and main by default, and all three for Rust. `with` gives the
jobs' inputs: each goes to every job that takes it, and an input no job
takes stops sync. A job runs on the events JOBS gives it unless `jobs`
narrows them, and `false` drops it, as a private repository drops review.
The browser job runs only when `with` gives a browser-test, and a
service's terraform and tests jobs only when it gives a terraform-dir and
a test. Rust's jobs are the ones the Rust crate standard asks for here,
from the rules and pubkit.json's exceptions, and sync writes each job's
required make targets into its workflow, so a change to either shows as a
change there. `release` adds pubrel's release workflow to pr.yml and
main.yml, at this pubkit's pubrel unless it names one; `false` leaves it
out, and a service, which deploys, has none. `{"uses": "local"}` calls the
repository's own .github/workflows/release.yml instead, as pubrel does, so
a change to its release workflow is tested before it ships.

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
from .refs import owner, ref, release_commit

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
    # Rust: the job of the Rust crate standard this one runs. Its required
    # targets, if it has any, go in the input `required`; with none, or a
    # workflow job the standard does not turn on, the job is left out.
    standard: str | None = None


NODE_TEST = ("node-version", "python-version", "apt-packages", "setup", "test", "sync-check",
             "coverage-report", "fuzz-runs", "fuzz-runs-scheduled")
PYTHON_TEST = ("python-version", "apt-packages", "setup", "test", "sync-check",
               "coverage-report", "fuzz-runs", "fuzz-runs-scheduled")
SONAR_INTERNAL = ("reports", "layout")
RUST_REQUIRED = ("required",)
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
                     fixed={"reports": "sonar.javascript.lcov.reportPaths=*"},
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
                     fixed={"reports": "sonar.python.coverage.reportPaths=*.xml"},
                     internal=SONAR_INTERNAL),
    },
    "rust": {
        "gates": Job("job-rust-gates.yml", ("python-version", "gates"), internal=RUST_REQUIRED,
                     standard="gates"),
        "test": Job("job-rust-test.yml", ("os-matrix", "test-args"), needs=("gates",),
                    standard="test"),
        "functional": Job("job-rust-functional.yml",
                          ("python-version", "apt-packages", "pub-version", "functional",
                           "cargo-llvm-cov-version"), needs=("gates",), secrets=True,
                          internal=RUST_REQUIRED, standard="functional"),
        "msrv": Job("job-rust-msrv.yml", needs=("gates",), standard="msrv"),
        "fuzz": Job("job-rust-fuzz.yml",
                    ("python-version", "fuzz-seconds", "fuzz-seconds-scheduled", "cargo-fuzz-version"),
                    needs=("gates",), standard="fuzz"),
        "cross": Job("job-rust-cross.yml", ("python-version", "cross-targets", "cross-test-args"),
                     needs=("gates",), standard="cross"),
        "audit": Job("job-rust-audit.yml", ("cargo-deny-version", "cargo-vet-version"),
                     internal=RUST_REQUIRED, standard="audit"),
        "osv": Job("job-osv.yml"),
        "review": Job("job-review.yml", events=("pr",)),
        "toolchains": Job("job-rust-toolchains.yml", ("scheduled-toolchains", "test-args"),
                          needs=("gates",), events=("nightly",)),
        "sonar": Job("job-sonar.yml", ("sonar-args",), needs=("functional",), secrets=True,
                     fixed={"reports": "sonar.rust.lcov.reportPaths=lcov.info "
                                       "sonar.python.coverage.reportPaths=*.xml"},
                     internal=SONAR_INTERNAL),
    },
    # A service deploys rather than releases, and is private: no release,
    # and no scan or dependency review, which need a public repository.
    "service": {
        "checks": Job("job-service-checks.yml", ("python-version", "shellcheck", "sync-check")),
        "terraform": Job("job-service-terraform.yml", ("terraform-dir",), when="terraform-dir"),
        "tests": Job("job-service-tests.yml", ("python-version", "setup", "test"), when="test"),
    },
}
# The languages a utility can be; a rust or service repository is its kind.
UTILITY = ("node", "python")
# Where SonarQube Cloud finds the code and its tests, by language: the
# directories that hold code, in the order tried, and those that hold
# tests. Without them it reads the whole repository as code, and warns that
# some of it looks like tests. Each is given only when the repository has
# it, since the scan fails on a directory that is not there.
LAYOUT = {"python": (("src",), ("tests",)),
          "rust": (("src", "crates"), ("tests", "fuzz", "benches"))}

# Inputs the release workflow takes besides pubrel-version, by language.
RELEASE_WITH = {"rust": {"semver-checks-version": "0.51.0"}}
# The events a language runs on unless the ci block names them: Rust adds
# the nightly, for longer fuzzing and the latest stable and beta Rust.
LANGUAGE_EVENTS = {"rust": EVENTS}


def stop(text: str) -> None:
    sys.exit(f"pubkit: pubkit.json's ci {text}")


def is_node(root: Path) -> bool:
    """A Node package: a package.json, and no pyproject.toml beside it."""
    return (root / "package.json").exists() and not (root / "pyproject.toml").exists()


def plan(config: dict[str, Any], root: Path) -> dict:
    """What the Rust crate standard requires here: the make targets of each
    job and the workflow jobs it turns on, as `pubkit required` gives them."""
    from . import conform, standard  # noqa: PLC0415
    rules = standard.load()
    waived, _, _ = conform.exceptions(config, {r["id"] for r in rules["rules"]})
    # Before there is a crate, every rule a library would meet.
    library = not (root / "Cargo.toml").exists() or standard.has_library(root)
    return standard.required(rules, library, frozenset(waived))


def planned(job: Job, rules: dict | None) -> bool:
    """Whether the standard asks for this job: some required targets, or a
    workflow job it turns on."""
    if job.standard is None or rules is None:
        return True
    if job.standard in rules["targets"]:
        return bool(rules["targets"][job.standard])
    return job.standard in rules["jobs"]


def settings(config: dict[str, Any], root: Path) -> dict[str, Any] | None:
    """The ci block, checked and filled in, or None when pubkit.json has
    none and the repository writes its own workflows."""
    given = config.get("ci")
    if given is None:
        return None
    kind = config.get("kind")
    if not isinstance(given, dict):
        stop('block must be an object, such as {"with": {"test": "npm test"}}')
    unknown = sorted(set(given) - set(SETTINGS))
    if unknown:
        stop(f"block has no setting {', '.join(unknown)}; it takes {', '.join(SETTINGS)}")
    if kind == "utility":
        language = given.get("language", "node" if is_node(root) else "python")
        if language not in UTILITY:
            stop(f"language must be one of {', '.join(UTILITY)}, not {language!r}")
    else:
        language = kind
        if "language" in given and given["language"] != kind:
            stop(f"language is for a utility; a {kind} repository's CI is {kind}'s")
    table = JOBS[language]
    events = given.get("events", list(LANGUAGE_EVENTS.get(language, DEFAULT_EVENTS)))
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
    rules = plan(config, root) if language == "rust" else None
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
        if not planned(job, rules) or (name == "cross" and inputs.get("cross-targets") == "[]"):
            continue
        jobs[name] = tuple(e for e in EVENTS if e in on and e in events)
    unknown = sorted(set(overrides) - set(table))
    if unknown:
        stop(f"jobs names {', '.join(unknown)}, which {language} has not; it has {', '.join(table)}")
    from .new import PUBREL_PIN  # noqa: PLC0415
    release = given.get("release", language != "service")
    if language == "service" and release is not False:
        stop("release is not for a service, which deploys rather than releases")
    if release is True:
        release = {}
    if release is not False:
        if not isinstance(release, dict) or set(release) - {"pubrel", "with", "uses"}:
            stop('release must be true, false or {"pubrel": "0.5.4", "with": {...}}')
        extra = release.get("with", {})
        if not isinstance(extra, dict):
            stop("release.with must be an object of the release workflow's inputs")
        if "uses" in release:
            # pubrel itself: its own release workflow, from the checkout.
            if release["uses"] != "local" or "pubrel" in release:
                stop('release.uses can only be "local", the repository\'s own '
                     '.github/workflows/release.yml, and then names no pubrel')
            release = {"local": True, "with": extra}
        else:
            pubrel = release.get("pubrel", PUBREL_PIN)
            if not isinstance(pubrel, str) or not pubrel[:1].isdigit():
                stop('release.pubrel must be a version, such as "0.5.4"')
            release = {"pubrel": pubrel, "with": {**RELEASE_WITH.get(language, {}), **extra}}
    return {"language": language, "events": [e for e in EVENTS if e in events], "cron": cron,
            "with": inputs, "jobs": jobs, "release": release, "layout": layout(language, root),
            "targets": rules["targets"] if rules else {}}


def layout(language: str, root: Path) -> str:
    """The sonar job's `layout`: sonar.sources and sonar.tests, from the
    directories the repository has; empty without tests to name. With no
    code directory the code is the repository, less its tests, so no file
    is read as both."""
    if language not in LAYOUT:
        return ""
    code, tests = ([d for d in dirs if (root / d).is_dir()] for dirs in LAYOUT[language])
    if not tests:
        return ""
    out = [f"-Dsonar.sources={','.join(code or ['.'])}", f"-Dsonar.tests={','.join(tests)}"]
    if not code:
        out.append(f"-Dsonar.exclusions={','.join(f'{d}/**' for d in tests)}")
    return " ".join(out)


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
        if "required" in job.internal:
            given["required"] = " ".join(ci["targets"][job.standard])
        if "layout" in job.internal and ci["layout"]:
            given["layout"] = ci["layout"]
        needs = [n for n in job.needs if n in names]
        out.append(f"{'' if name == names[0] else chr(10)}  {name}:\n    name: ci\n")
        if needs:
            out.append(f"    needs: [{', '.join(needs)}]\n")
        out.append(f"    uses: {ci['owner']}/pubkit/.github/workflows/{job.workflow}"
                   f"@{ref(__version__, ci['commit'])}\n")
        if given:
            out.append(f"    with: {flow(given)}\n")
        # By name, not `secrets: inherit`: GitHub hands inherited secrets
        # only to a reusable workflow of the caller's own owner, and drops
        # them without a word for pubkit's, once the two owners differ.
        if job.secrets:
            out.append("    secrets: { SONAR_TOKEN: '${{ secrets.SONAR_TOKEN }}' }\n")
    out.append(
        "\n  # The one check a ruleset requires: it passes only when every job\n"
        "  # above succeeded. None of them is ever skipped for not being asked for.\n"
        "  passed:\n    name: ci / passed\n    if: always()\n"
        f"    needs: [{', '.join(names)}]\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - run: |\n          echo \"$RESULTS\"\n"
        "          echo \"$RESULTS\" | jq -e 'all(.[]; .result == \"success\")' > /dev/null\n"
        "        env: { RESULTS: '${{ toJSON(needs) }}' }\n")
    release = ci["release"]
    if release and event in RELEASE_EVENTS and release.get("local"):
        out.append(
            "\n  # The repository's own release workflow, built from this checkout,\n"
            "  # so a change to it is tested here before a release carries it to\n"
            "  # anyone else.\n"
            "  release:\n    needs: passed\n    uses: ./.github/workflows/release.yml\n"
            + (f"    with: {flow(release['with'])}\n" if release["with"] else "")
            + "    permissions:\n      contents: write\n")
    elif release and event in RELEASE_EVENTS:
        pubrel = release["pubrel"]
        out.append(
            "\n  # pubrel's release workflow: on a pull request, a code change records\n"
            "  # what it changes and a release is the bump its changes require; on\n"
            "  # main, a published version is tagged and released.\n"
            "  release:\n    needs: passed\n"
            f"    uses: {ci['owner']}/pubrel/.github/workflows/release.yml"
            f"@{ref(pubrel, release['commit'])}\n"
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
    ci["owner"] = owner(config)
    ci["commit"] = release_commit()
    if ci["release"] and not ci["release"].get("local"):
        from .refs import pubrel_commit  # noqa: PLC0415
        written = "".join((root / path(e)).read_text(encoding="utf-8") for e in EVENTS
                          if (root / path(e)).is_file())
        ci["release"]["commit"] = pubrel_commit(ci["release"]["pubrel"], written, ci["owner"])
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
