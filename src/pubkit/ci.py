"""The CI `pubkit sync` writes from the `ci` block of pubkit.json.

A reusable workflow that serves every repository has to skip the jobs a
repository does not use, and GitHub shows each as skipped. Here pubkit
writes the repository's workflows instead, one per event, each calling
only the jobs that event runs: one reusable workflow per job
(job-*.yml), pinned to the commit of this pubkit's release tag, with the
version in a comment (refs.py).

    "ci": {
      "language": "node",
      "events": ["pr", "queue", "main", "nightly"],
      "cron": "17 3 * * *",
      "with": {"browser-test": "npm run test:browser", "coverage-report": "coverage/"},
      "jobs": {"review": false, "sonar": {"events": ["pr", "main"]}},
      "release": {"pubrel": "0.5.4", "with": {}},
      "tools": [{"crate": "some-cli", "git": "https://github.com/example-org/some-cli",
                 "tag": "v1.2.3"}]
    }

`language` is a utility's, node or python; without it, a package.json
with no pyproject.toml beside it makes it node. A rust, service or content
repository's CI is its kind's. `events` are the workflows written: pr.yml
(pull requests), queue.yml (the merge queue's runs of them, `merge_group`,
so the checks a ruleset requires report on the queue's merge too),
main.yml (pushes to main) and nightly.yml (the schedule `cron`, and by
hand); pr, queue and main by default, and all four for Rust. The queue
runs every job a pull request does, and the heavy ones (Rust's msrv, fuzz
and cross) besides, so a pull request gets its answer sooner and nothing
merges untested. Where the queue would run nothing a pull request does
not, it has no workflow of its own: pr.yml runs on `merge_group` too. A
repository without a merge queue (a user's, or a private one) leaves
`queue` out, and its pull requests run the heavy jobs. `with` gives the
jobs' inputs: each goes to every job that takes it, and an input no job
takes stops sync. A job runs on the events JOBS gives it unless `jobs`
narrows them, and `false` drops it, as a private repository drops review.
The browser job runs only when `with` gives a browser-test, and a
service's terraform and tests jobs only when it gives a terraform-dir and
a test. Rust's jobs are the ones the Rust crate standard asks for here,
from the rules and pubkit.json's exceptions, and sync writes each job's
required make targets into its workflow, so a change to either shows as a
change there. `release` adds pubrel's release workflow to pr.yml,
queue.yml and main.yml, at this pubkit's pubrel unless it names one, passing it the
release app's RELEASE_APP_ID and RELEASE_APP_KEY from pubrel 0.6.1, the
first to take them, so `pubrel tag` tags with the app the tag ruleset lets
through; `false` leaves it
out, and a service, which deploys, has none, nor has a content repository,
which pubkit neither releases nor deploys. A content repository's CI is
its checks (the managed files in step, and shellcheck on its **/*.sh
unless `with` gives shellcheck) and, when `with` gives a test, the tests
job, which runs the checks its own tooling makes; it has no scan. `{"uses": "local"}` calls the
repository's own .github/workflows/release.yml instead, as pubrel does, so
a change to its release workflow is tested before it ships.

`tools` lists what a Rust repository's functional suite drives: each crate
is installed from its git repository at its tag, by `cargo install --locked`,
and the nightly moves each tag to the latest release of that repository.

`runs-on` names the runners: a label, such as a self-hosted runner scale
set's, for every job, or an object of labels by job name with "default" for
the rest, such as {"default": "my-runners", "review": "ubuntu-latest"}. It
goes to each job pubkit calls as its `runs-on` input, and into the jobs
pubkit writes itself (tested, passed, site). Rust's test job runs on its
os-matrix instead. pubrel's release workflow takes it from pubrel 0.7.0,
which installs Rust on a runner without it; an older pubrel's runs on
GitHub's runners. Without it nothing is written, and every job runs on
ubuntu-latest.

A repository that deploys a static site with a deploy-static action (a
workflow of its own uses it, or pubkit.json has a `site` block) gets a `site` job in pr.yml (and queue.yml): it builds the site
(`site.pr_build`, else `site.build`, else nothing) and runs the
deploy-static its deploy workflow pins, with check-only, on
`site.check_dir` (else the `dir` that workflow gives it). A site that would
fail to deploy fails the pull request.

    "site": {"check_dir": "public", "pr_build": "npm run build:sample",
             "url": "https://example.eu/"}

Each workflow ends in `passed` (the check a ruleset requires, `ci /
passed`), which passes only when every job it needs succeeded.

The merge queue runs queue.yml (or pr.yml) on the pull request merged
into main, and merging moves main to that very commit, so main.yml would
test it again. It starts instead with `tested` (job-tested.yml), which
looks for a successful merge_group run of that workflow on the commit:
found, main skips the jobs the queue ran, and runs the scan (with the queue run's coverage,
since SonarQube Cloud measures main from a push to it) and the release;
not found, as without a queue, every job runs as before. The merge queue
never scans: SonarQube Cloud analyses pull requests and main and refuses
the queue's gh-readonly-queue branch, the pull request's run scanned the
change, and main's push scans the merge. A Rust queue.yml has no sonar
job, and in pr.yml, which the queue runs too, it does not run on
merge_group, which `passed` takes. Every job
is named `ci`, so its checks read `ci / test`, `ci / sonar` and so on.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__
from .refs import owner, ref, release_commit

EVENTS = ("pr", "queue", "main", "nightly")
DEFAULT_EVENTS = ("pr", "queue", "main")
# The jobs too slow to hold up every push to a pull request: they run in
# the merge queue, before anything merges, and on main and the nightly.
HEAVY = ("queue", "main", "nightly")
CRON = "17 3 * * *"
TRIGGERS = {
    "pr": "  pull_request:\n",
    "queue": "  merge_group:\n",
    "main": "  push: { branches: [main] }\n",
    "nightly": "  schedule: [{ cron: 'CRON' }]\n  workflow_dispatch:\n",
}
# The events the release workflow runs on: a pull request records and
# checks its release; a push to main tags and releases it. The merge
# queue's run of a pull request (merge_group) only reports the check:
# pubrel tags from a push to main alone.
RELEASE_EVENTS = ("pr", "queue", "main")
# The events the site is checked on, before anything deploys it.
SITE_EVENTS = ("pr", "queue")
# Jobs the merge queue never runs: SonarQube Cloud analyses pull requests
# and main, and refuses the queue's gh-readonly-queue branch. The pull
# request's run scanned the change, and main's push scans the merge.
NOT_QUEUED = ("sonar",)
SETTINGS = ("language", "events", "cron", "with", "jobs", "release", "tools", "runs-on")
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
    # Inputs this kind gives unless the ci block's `with` does: the
    # workflow's own default suits another kind.
    defaults: dict[str, str] = field(default_factory=dict)
    # Rust: the job of the Rust crate standard this one runs. Its required
    # targets, if it has any, go in the input `required`; with none, or a
    # workflow job the standard does not turn on, the job is left out.
    standard: str | None = None
    # Its runners come from an input of its own (os-matrix), so `runs-on`
    # does not reach it.
    matrix: bool = False


NODE_TEST = ("node-version", "python-version", "apt-packages", "setup", "test", "sync-check",
             "coverage-report", "fuzz-runs", "fuzz-runs-scheduled")
PYTHON_TEST = ("python-version", "apt-packages", "setup", "test", "sync-check",
               "coverage-report", "fuzz-runs", "fuzz-runs-scheduled")
SONAR_INTERNAL = ("reports", "layout", "coverage-run")
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
                    standard="test", matrix=True),
        "functional": Job("job-rust-functional.yml",
                          ("python-version", "apt-packages", "functional",
                           "cargo-llvm-cov-version"), needs=("gates",), secrets=True,
                          internal=(*RUST_REQUIRED, "tools"), standard="functional"),
        "msrv": Job("job-rust-msrv.yml", needs=("gates",), events=HEAVY, standard="msrv"),
        "fuzz": Job("job-rust-fuzz.yml",
                    ("python-version", "fuzz-seconds", "fuzz-seconds-scheduled", "cargo-fuzz-version"),
                    needs=("gates",), events=HEAVY, standard="fuzz"),
        "cross": Job("job-rust-cross.yml", ("python-version", "cross-targets", "cross-test-args"),
                     needs=("gates",), events=HEAVY, standard="cross"),
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
    # A service deploys rather than releases: no release. Public code is
    # scanned, a service's too; a private service, which SonarQube Cloud
    # does not analyse for free, drops the scan with "jobs": {"sonar": false}.
    "service": {
        "checks": Job("job-service-checks.yml", ("python-version", "shellcheck", "sync-check")),
        "terraform": Job("job-service-terraform.yml", ("terraform-dir",), when="terraform-dir"),
        "tests": Job("job-service-tests.yml", ("python-version", "setup", "test", "coverage-report"),
                     when="test"),
        "sonar": Job("job-sonar.yml", ("sonar-args",), needs=("tests",), secrets=True,
                     fixed={"reports": "sonar.python.coverage.reportPaths=*.xml "
                                       "sonar.javascript.lcov.reportPaths=lcov.info"},
                     internal=SONAR_INTERNAL),
    },
    # Authored content, private, that pubkit neither releases nor deploys:
    # the managed files in step and its shell scripts checked, and its own
    # tooling's checks as the tests job, when it names a test. No scan: there
    # is no code for SonarQube Cloud to analyse, and it would not analyse a
    # private repository for free.
    "content": {
        "checks": Job("job-service-checks.yml", ("python-version", "shellcheck", "sync-check"),
                      defaults={"shellcheck": "**/*.sh"}),
        "tests": Job("job-service-tests.yml", ("python-version", "setup", "test", "coverage-report"),
                     when="test"),
    },
}
# Kinds that never release: a service deploys, and content is versioned, if
# at all, by its own tooling.
UNRELEASED = {"service": "a service, which deploys rather than releases",
              "content": "a content repository, which pubkit neither releases nor deploys"}
# The languages a utility can be; a rust, service or content repository is its kind.
UTILITY = ("node", "python")
# Where SonarQube Cloud finds the code and its tests, by language: the
# directories that hold code, in the order tried, and those that hold
# tests. Without them it reads the whole repository as code, and warns that
# some of it looks like tests. Each is given only when the repository has
# it, since the scan fails on a directory that is not there.
LAYOUT = {"python": (("src",), ("tests",)),
          "rust": (("src", "crates"), ("tests", "fuzz", "benches"))}

# What a tool in the ci block's `tools` is: what cargo installs, and from where.
TOOL = ("crate", "git", "tag")
CRATE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
TOOL_GIT = re.compile(r"https://[A-Za-z0-9.-]+(?::[0-9]+)?(?:/[A-Za-z0-9_.-]+)+/?")
TOOL_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]*")
# The git repositories whose latest release the nightly can read.
GITHUB_REPO = re.compile(r"https://github\.com/(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)"
                         r"(?:\.git)?/?")

# The checkout the site job runs, pinned as pubkit's own workflows pin it.
CHECKOUT = "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5.1.0"
SITE = ("check_dir", "pr_build", "build", "url")
# A use of a deploy-static action, from whichever repository ships it,
# with what follows `@` up to the end of the line: the site job runs that
# same pin, so pubkit holds no pin of its own for it.
DEPLOY_STATIC = re.compile(r"(?P<uses>[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+/actions/deploy-static@[^\s#]+"
                           r"(?:[ \t]+#[ \t]*v[0-9][^\s]*)?)")
# deploy-static's `dir`, in the lines of the step that uses it.
DEPLOY_DIR = re.compile(r"""^\s*(?:with:\s*\{[^}\n]*?\b)?dir:\s*['"]?([^'"\s,}]+)""", re.M)

# The first pubrel whose release workflow takes the release app's secrets.
# Passing a secret a reusable workflow does not declare fails the caller.
RELEASE_APP_FROM = (0, 6, 1)
RELEASE_SECRETS = ("    secrets: { RELEASE_APP_ID: '${{ secrets.RELEASE_APP_ID }}', "
                   "RELEASE_APP_KEY: '${{ secrets.RELEASE_APP_KEY }}' }\n")


def pubrel_from(pubrel: str, first: tuple[int, int, int]) -> bool:
    """Whether pubrel `pubrel` is release `first` or later."""
    parts = []
    for part in pubrel.split("-", 1)[0].split(".")[:3]:
        if not part.isdigit():
            return False
        parts.append(int(part))
    return tuple(parts) >= first


def takes_app(pubrel: str) -> bool:
    """Whether pubrel `pubrel`'s release workflow takes the app's secrets."""
    return pubrel_from(pubrel, RELEASE_APP_FROM)


# The first pubrel whose release workflow takes runs-on.
RELEASE_RUNS_ON_FROM = (0, 7, 0)


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
    if "queue" in events and "pr" not in events:
        stop("events lists queue without pr: the merge queue runs pull requests")
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
    listed = tools(given)
    if listed and language != "rust":
        stop(f"tools is for a Rust repository's functional job; {language} has none")
    runners = runs_on(given, table)
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
        jobs[name] = tuple(e for e in EVENTS if e in on and (e in events or e == "queue")
                           and not (name in NOT_QUEUED and e == "queue"))
    unknown = sorted(set(overrides) - set(table))
    if unknown:
        stop(f"jobs names {', '.join(unknown)}, which {language} has not; it has {', '.join(table)}")
    # The queue has a workflow of its own only when it runs a job pull
    # requests do not; else pr.yml runs in the queue too, with the queue's
    # jobs. Without a queue, its jobs run on pull requests instead.
    separate = "queue" in events and any("queue" in on and "pr" not in on for on in jobs.values())
    if not separate:
        events = [e for e in events if e != "queue"]
        jobs = {name: tuple(e for e in EVENTS if e in events and (
                    e in on or (e == "pr" and "queue" in on))) for name, on in jobs.items()}
    from .new import PUBREL_PIN  # noqa: PLC0415
    release = given.get("release", language not in UNRELEASED)
    if language in UNRELEASED and release is not False:
        stop(f"release is not for {UNRELEASED[language]}")
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
            "site": site(config, root), "tools": listed, "runs-on": runners,
            "targets": rules["targets"] if rules else {}}


# A runner label: GitHub's, such as ubuntu-latest, or a self-hosted runner
# scale set's name.
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
# The jobs pubkit writes into the workflow itself, besides the table's.
OWN_JOBS = ("tested", "passed", "site", "release")


def runs_on(given: dict[str, Any], table: dict[str, Job]) -> dict[str, str]:
    """The ci block's `runs-on`, checked: the runner label of each job that
    has one, by job name, under "default" for every other. Empty without
    it, so nothing is written and every job runs on ubuntu-latest."""
    value = given.get("runs-on")
    if value is None:
        return {}
    if isinstance(value, str):
        value = {"default": value}
    if not isinstance(value, dict) or not value:
        stop('runs-on must be a runner label, such as "my-runners", or an object of them '
             'by job, such as {"default": "my-runners", "review": "ubuntu-latest"}')
    named = [n for n, job in table.items() if not job.matrix]
    allowed = ("default", *named, *OWN_JOBS)
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        matrix = sorted(n for n in unknown if n in table)
        if matrix:
            stop(f"runs-on names {', '.join(matrix)}, whose runners its os-matrix input gives")
        stop(f"runs-on names {', '.join(unknown)}; it takes {', '.join(allowed)}")
    for name, label in value.items():
        if not isinstance(label, str) or not LABEL.fullmatch(label):
            stop(f"runs-on.{name} must be a runner label, such as \"ubuntu-latest\", "
                 f"not {json.dumps(label)}")
    return value


def runner(ci: dict[str, Any], name: str) -> str | None:
    """The label job `name` runs on where the ci block gives one."""
    return ci["runs-on"].get(name, ci["runs-on"].get("default"))


def tools(given: dict[str, Any]) -> list[dict[str, str]]:
    """The ci block's `tools`, checked; each is a crate, a git and a tag."""
    listed = given.get("tools", [])
    if not isinstance(listed, list):
        stop('tools must be a list of objects, each with a crate, a git and a tag')
    for tool in listed:
        if not isinstance(tool, dict):
            stop(f"tools must list objects, not {json.dumps(tool)}")
        unknown = sorted(set(tool) - set(TOOL))
        if unknown:
            stop(f"tools has no setting {', '.join(unknown)}; each tool takes {', '.join(TOOL)}")
        for key, pattern, what in (("crate", CRATE, "a crate's name"),
                                   ("git", TOOL_GIT, "the git repository's https:// address"),
                                   ("tag", TOOL_TAG, "a tag of that repository")):
            if not isinstance(tool.get(key), str) or not pattern.fullmatch(tool[key]):
                stop(f"tools: {key} must be {what}, not {json.dumps(tool.get(key))}")
    crates = [tool["crate"] for tool in listed]
    if len(set(crates)) != len(crates):
        stop("tools lists a crate twice")
    return listed


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


def site(config: dict[str, Any], root: Path) -> dict[str, str] | None:
    """The site job's settings: pubkit.json's site block, checked, filled
    in from the workflow that runs deploy-static, and the deploy-static
    that workflow pins (`uses`); None when the repository deploys no static
    site with it."""
    given = config.get("site")
    deploys = [p for p in sorted((root / ".github" / "workflows").glob("*.y*ml"))
               if not is_generated(text := p.read_text(encoding="utf-8"))
               and DEPLOY_STATIC.search(text)]
    if given is None and not deploys:
        return None

    def refuse(text: str) -> None:
        sys.exit(f"pubkit: pubkit.json's site {text}")

    given = {} if given is None else given
    if not isinstance(given, dict):
        refuse('block must be an object, such as {"check_dir": "public"}')
    unknown = sorted(set(given) - set(SITE))
    if unknown:
        refuse(f"block has no setting {', '.join(unknown)}; it takes {', '.join(SITE)}")
    for key, value in given.items():
        if not isinstance(value, str) or not value.strip() or "\n" in value:
            refuse(f"{key} must be one line of text")
    if "url" in given and not re.fullmatch(r"https://[^\s/]+/\S*", given["url"]):
        refuse("url must be the site's https:// address, such as https://example.eu/")
    out = dict(given)
    if "check_dir" not in out:
        found = []
        for path in deploys:
            text = path.read_text(encoding="utf-8")
            for m in DEPLOY_STATIC.finditer(text):
                if d := DEPLOY_DIR.search("\n".join(text[m.end():].splitlines()[:12])):
                    found.append(d[1])
        if len(set(found)) != 1:
            refuse("block needs check_dir, the directory deploy-static deploys: "
                   + (f"the workflows give {', '.join(sorted(set(found)))}" if found
                      else "no workflow's deploy-static step gives one pubkit can read"))
        out["check_dir"] = found[0]
    rel = Path(out["check_dir"])
    if rel.is_absolute() or ".." in rel.parts:
        refuse("check_dir must be a directory inside the repository")
    pins = sorted({m["uses"] for path in deploys
                   for m in DEPLOY_STATIC.finditer(path.read_text(encoding="utf-8"))})
    if len(pins) != 1:
        refuse("block needs the deploy-static its deploy workflow pins, for the pull request's "
               "check to run the same one: "
               + (f"the workflows pin {', '.join(pins)}; pin one" if pins
                  else "no workflow of the repository's own uses deploy-static"))
    out["uses"] = pins[0]
    return out


def flow(values: dict[str, Any]) -> str:
    """A `with:` mapping on one line: JSON, which YAML reads as it is."""
    return json.dumps(values, ensure_ascii=False)


# The condition each testing job runs under, on main and in the merge
# queue: no earlier run already passed on the commit's tree.
UNTESTED = "needs.tested.outputs.tested != 'true'"
NO_QUEUE = "github.event_name != 'merge_group'"


def queued(ci: dict[str, Any]) -> str:
    """The event whose workflow the merge queue runs."""
    return "queue" if "queue" in ci["events"] else "pr"


def tested_job(ci: dict[str, Any]) -> str:
    return ("  # Whether an earlier run already passed on this commit's tree: then the\n"
            "  # jobs it ran are skipped, and nothing here tests less than it did.\n"
            "  tested:\n    name: ci\n"
            f"    uses: {ci['owner']}/pubkit/.github/workflows/job-tested.yml"
            f"@{ref(__version__, ci['commit'])}\n"
            f"    with: {flow({'workflow': path(queued(ci)), **runs_with(ci, 'tested')})}\n"
            "    permissions:\n      actions: read\n      contents: read\n")


def runs_with(ci: dict[str, Any], name: str) -> dict[str, str]:
    """The `runs-on` input of a job pubkit calls, where the ci block gives
    it a runner; nothing else, so the job keeps its default."""
    label = runner(ci, name)
    return {"runs-on": label} if label else {}


def trigger(ci: dict[str, Any], event: str) -> str:
    """What starts one event's workflow: pr.yml's runs in the merge queue
    too when the queue has none of its own."""
    out = TRIGGERS[event].replace("CRON", ci["cron"])
    if event == "pr" and queued(ci) == "pr":
        out += TRIGGERS["queue"]
    return out


def workflow(ci: dict[str, Any], event: str) -> str:
    """The text of one event's workflow."""
    table = JOBS[ci["language"]]
    names = [name for name, on in ci["jobs"].items() if event in on]
    # Main takes an earlier run of the queue's workflow on the same tree, and
    # so does the queue, where it runs pr.yml itself: a pull request up to
    # date with main merges as the very files its own run tested.
    shortcut = (event == "main" and "pr" in ci["events"]) or (event == "pr" and queued(ci) == "pr")
    out = [HEADER, f"name: {event}\n\non:\n", trigger(ci, event),
           "\npermissions:\n  contents: read\n\njobs:\n"]
    if shortcut:
        out.append(tested_job(ci))
    for name in names:
        job = table[name]
        given = {**job.defaults, **{i: ci["with"][i] for i in job.inputs if i in ci["with"]},
                 **job.fixed}
        if "required" in job.internal:
            given["required"] = " ".join(ci["targets"][job.standard])
        if "tools" in job.internal and ci["tools"]:
            given["tools"] = json.dumps(ci["tools"])
        if "layout" in job.internal and ci["layout"]:
            given["layout"] = ci["layout"]
        if not job.matrix:
            given.update(runs_with(ci, name))
        needs = [n for n in job.needs if n in names]
        # On main, a job the queue ran waits to hear whether it passed.
        # In the queue, a tree its pull request already passed is not scanned
        # again either: main's push scans it, with that run's coverage.
        skips = shortcut and (queued(ci) in ci["jobs"][name] or (name == "sonar" and event != "main"))
        if skips or (shortcut and name == "sonar"):
            needs = ["tested", *needs]
        first = name == names[0] and not shortcut
        out.append(f"{'' if first else chr(10)}  {name}:\n    name: ci\n")
        if needs:
            out.append(f"    needs: [{', '.join(needs)}]\n")
        scan = shortcut and name == "sonar" and event == "main"
        if scan:
            # The scan runs either way, after the tests or in their place.
            out.append("    if: ${{ !failure() && !cancelled() }}\n")
            given["coverage-run"] = "${{ needs.tested.outputs.run-id }}"
        elif skips and name in NOT_QUEUED and event == "pr":
            # pr.yml runs in the merge queue too: there, never.
            out.append(f"    if: ${{{{ {NO_QUEUE} && {UNTESTED} }}}}\n")
        elif skips:
            out.append(f"    if: ${{{{ {UNTESTED} }}}}\n")
        out.append(f"    uses: {ci['owner']}/pubkit/.github/workflows/{job.workflow}"
                   f"@{ref(__version__, ci['commit'])}\n")
        if scan:
            out.append("    permissions:\n      actions: read\n      contents: read\n")
        if given:
            out.append(f"    with: {flow(given)}\n")
        # By name, not `secrets: inherit`: GitHub hands inherited secrets
        # only to a reusable workflow of the caller's own owner, and drops
        # them without a word for pubkit's, once the two owners differ.
        if job.secrets:
            out.append("    secrets: { SONAR_TOKEN: '${{ secrets.SONAR_TOKEN }}' }\n")
    if ci["site"] and event in SITE_EVENTS:
        names = [*names, "site"]
        out.append(site_job(ci))
    if shortcut and event == "pr" and any(n in NOT_QUEUED for n in names):
        out.append(
            "\n  # The one check a ruleset requires: it passes only when every job\n"
            "  # above succeeded or, where an earlier run already passed on the\n"
            "  # commit's tree, was skipped for it, or is the scan, which the merge\n"
            "  # queue never runs. None is skipped for not being asked for.\n"
            "  passed:\n    name: ci / passed\n    if: always()\n"
            f"    needs: [{', '.join(['tested', *names])}]\n    runs-on: {runner(ci, 'passed') or 'ubuntu-latest'}\n"
            "    steps:\n"
            "      - run: |\n          echo \"$RESULTS\"\n"
            "          echo \"$RESULTS\" | jq -e --arg tested \"$TESTED\" --arg event \"$EVENT\" '.tested.result == \"success\"\n"
            "            and all(to_entries[]; .value.result == \"success\" or (.value.result == \"skipped\"\n"
            f"              and ($tested == \"true\" or (.key == \"{NOT_QUEUED[0]}\" and $event == \"merge_group\"))))'"
            " > /dev/null\n"
            "        env: { RESULTS: '${{ toJSON(needs) }}', TESTED: '${{ needs.tested.outputs.tested }}', "
            "EVENT: '${{ github.event_name }}' }\n")
    elif shortcut:
        out.append(
            "\n  # The one check a ruleset requires: it passes only when every job\n"
            "  # above succeeded or, where an earlier run already passed on the\n"
            "  # commit's tree, was skipped for it. None is skipped for not being asked for.\n"
            "  passed:\n    name: ci / passed\n    if: always()\n"
            f"    needs: [{', '.join(['tested', *names])}]\n    runs-on: {runner(ci, 'passed') or 'ubuntu-latest'}\n"
            "    steps:\n"
            "      - run: |\n          echo \"$RESULTS\"\n"
            "          echo \"$RESULTS\" | jq -e --arg tested \"$TESTED\" '.tested.result == \"success\"\n"
            "            and all(.[]; .result == \"success\" or (.result == \"skipped\" and $tested == \"true\"))'"
            " > /dev/null\n"
            "        env: { RESULTS: '${{ toJSON(needs) }}', TESTED: '${{ needs.tested.outputs.tested }}' }\n")
    else:
        out.append(
            "\n  # The one check a ruleset requires: it passes only when every job\n"
            "  # above succeeded. None of them is ever skipped for not being asked for.\n"
            "  passed:\n    name: ci / passed\n    if: always()\n"
            f"    needs: [{', '.join(names)}]\n    runs-on: {runner(ci, 'passed') or 'ubuntu-latest'}\n    steps:\n"
            "      - run: |\n          echo \"$RESULTS\"\n"
            "          echo \"$RESULTS\" | jq -e 'all(.[]; .result == \"success\")' > /dev/null\n"
            "        env: { RESULTS: '${{ toJSON(needs) }}' }\n")
    # A job skipped upstream skips the release too, unless it says otherwise.
    after = ("    if: ${{ !cancelled() && needs.passed.result == 'success' }}\n"
             if shortcut else "")
    release = ci["release"]
    if release and event in RELEASE_EVENTS and release.get("local"):
        out.append(
            "\n  # The repository's own release workflow, built from this checkout,\n"
            "  # so a change to it is tested here before a release carries it to\n"
            "  # anyone else.\n"
            "  release:\n    needs: passed\n" + after + "    uses: ./.github/workflows/release.yml\n"
            + (f"    with: {flow(release['with'])}\n" if release["with"] else "")
            + RELEASE_SECRETS
            + "    permissions:\n      contents: write\n")
    elif release and event in RELEASE_EVENTS:
        pubrel = release["pubrel"]
        out.append(
            "\n  # pubrel's release workflow: on a pull request, a code change records\n"
            "  # what it changes and a release is the bump its changes require; on\n"
            "  # main, a published version is tagged and released.\n"
            "  release:\n    needs: passed\n" + after +
            f"    uses: {ci['owner']}/pubrel/.github/workflows/release.yml"
            f"@{ref(pubrel, release['commit'])}\n"
            f"    with: {flow({'pubrel-version': f'v{pubrel}', **release['with'], **release_runner(ci, pubrel)})}\n"
            + (RELEASE_SECRETS if takes_app(pubrel) else "")
            + "    permissions:\n      contents: write\n")
    return "".join(out)


def release_runner(ci: dict[str, Any], pubrel: str) -> dict[str, str]:
    """The release job's runs-on input: the ci block's runner for it, where
    that pubrel's release workflow takes one (0.7.0 on); before that it
    always runs on ubuntu-latest."""
    return runs_with(ci, "release") if pubrel_from(pubrel, RELEASE_RUNS_ON_FROM) else {}


def site_job(ci: dict[str, Any]) -> str:
    """The pull request's site job: the site built as it is for a deploy,
    and checked by the deploy-static its deploy workflow pins, which deploys nothing."""
    s = ci["site"]
    build = s.get("pr_build") or s.get("build")
    given = {"dir": s["check_dir"], "check-only": "true", **({"site-url": s["url"]} if "url" in s else {})}
    return ("\n  # The site as deploy-static checks it before a deploy, so a site that\n"
            "  # would fail to deploy fails the pull request. It deploys nothing.\n"
            f"  site:\n    name: ci / site\n    runs-on: {runner(ci, 'site') or 'ubuntu-latest'}\n    steps:\n"
            f"      - uses: {CHECKOUT}\n"
            + (f"      - run: {json.dumps(build)}\n" if build else "")
            + f"      - uses: {s['uses']}\n"
            f"        with: {flow(given)}\n")


def path(event: str) -> str:
    return f".github/workflows/{event}.yml"


INTEGRATIONS = "integrations.json"
WITH_SECRETS = "with-secrets"


def declared(root: Path, wanted: list[tuple[str, str]]) -> tuple[str, str] | None:
    """integrations.json as it must read for the integrations scan to pass: every
    workflow pubkit writes that takes SONAR_TOKEN listed under a sonarcloud use, adding
    them to "sonarcloud.scan" where none lists them. None where the repository keeps no
    integrations.json, or one that isn't JSON, which the scan reports itself. A path
    already listed, by name or by a pattern, leaves the file as it is, byte for byte;
    nothing else is taken out, since something else may need it.

    The one path moved: the with-secrets pubkit writes runs `./pubsec run` and
    no longer calls bws, so the scan calls a Bitwarden use declared on it stale. Where
    pubkit writes it, such a use names with-secrets.json, which the scan reads, instead."""
    path = root / INTEGRATIONS
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
        uses = data["uses"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(uses, list) or not all(isinstance(u, dict) for u in uses):
        return None
    from fnmatch import fnmatch  # noqa: PLC0415
    listed = [p for u in uses if str(u.get("plugin", "")).startswith("sonarcloud.")
              for p in u.get("paths") or [] if isinstance(p, str)]
    missing = [name for name, body in wanted
               if name.startswith(".github/workflows/") and "secrets.SONAR_TOKEN" in body
               and not any(name == p or fnmatch(name, p) for p in listed)]
    moved = False
    if any(name == WITH_SECRETS for name, _ in wanted):
        for use in uses:
            paths = use.get("paths")
            if (str(use.get("plugin", "")).startswith("bitwarden.") and isinstance(paths, list)
                    and WITH_SECRETS in paths):
                use["paths"] = sorted({*(p for p in paths if p != WITH_SECRETS), f"{WITH_SECRETS}.json"})
                moved = True
    if not missing and not moved:
        return INTEGRATIONS, text
    if not missing:
        return INTEGRATIONS, json.dumps(data, indent=2) + "\n"
    scan = next((u for u in uses if u.get("plugin") == "sonarcloud.scan"), None)
    if scan is None:
        scan = {"plugin": "sonarcloud.scan", "paths": [], "mode": "required"}
        uses.append(scan)
        uses.sort(key=lambda u: str(u.get("plugin", "")))
    scan["paths"] = sorted({*scan.get("paths", []), *missing})
    return INTEGRATIONS, json.dumps(data, indent=2) + "\n"


REVIEW_LICENSES = ".github/dependency-review-licenses.txt"
CALLED = re.compile(r"""^[ \t-]*uses:[ \t]*['"]?(?P<owner>[A-Za-z0-9-]+)/(?P<path>[^@\s'"]+)@""", re.M)


def review_licenses(config: dict[str, Any], root: Path,
                    wanted: list[tuple[str, str]]) -> tuple[str, str] | None:
    """The review job's license exceptions, where a workflow pubkit writes runs
    job-review.yml: every reusable workflow and action of the owner's that this
    repository's workflows call, as the purl GitHub's dependency graph gives it,
    without its version. GitHub reads no license for them, and the action takes
    no wildcard, so each is named; the job keeps only the owner's."""
    bodies = {p: b for p, b in wanted if p.startswith(".github/workflows/")}
    if not any("/job-review.yml@" in b for b in bodies.values()):
        return None
    for found in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        bodies.setdefault(f".github/workflows/{found.name}", found.read_text(encoding="utf-8"))
    own = owner(config)
    purls = sorted({f"pkg:githubactions/{m['owner']}/{m['path']}" for body in bodies.values()
                    for m in CALLED.finditer(body) if m["owner"] == own})
    return REVIEW_LICENSES, ("# Written by `pubkit sync` from this repository's workflows. Do not edit:\n"
                             "# the review job leaves these out of its license check, and no other.\n"
                             + "".join(f"{p}\n" for p in purls))


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
