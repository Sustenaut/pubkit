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
with no pyproject.toml beside it makes it node. A rust or service
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
out, and a service, which deploys, has none. `{"uses": "local"}` calls the
repository's own .github/workflows/release.yml instead, as pubrel does, so
a change to its release workflow is tested before it ships.

`tools` lists what a Rust repository's functional suite drives: each crate
is installed from its git repository at its tag, by `cargo install --locked`,
and the nightly moves each tag to the latest release of that repository. It
replaces the `pub-version` input, which still works and warns.

A repository that deploys a static site with pubkit's deploy-static (a
workflow of its own uses it, or pubkit.json has a `site` block) gets a
`site` job in pr.yml (and queue.yml): it builds the site (`site.pr_build`, else
`site.build`, else nothing) and runs deploy-static, at this pubkit, with
check-only, on `site.check_dir` (else the `dir` its deploy workflow gives
deploy-static). A site that would fail to deploy fails the pull request.

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
not found, as without a queue, every job runs as before. Every job
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
SETTINGS = ("language", "events", "cron", "with", "jobs", "release", "tools")
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
                    standard="test"),
        "functional": Job("job-rust-functional.yml",
                          ("python-version", "apt-packages", "pub-version", "functional",
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
DEPLOY_STATIC = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/pubkit/actions/deploy-static@")
# deploy-static's `dir`, in the lines of the step that uses it.
DEPLOY_DIR = re.compile(r"""^\s*(?:with:\s*\{[^}\n]*?\b)?dir:\s*['"]?([^'"\s,}]+)""", re.M)

# The first pubrel whose release workflow takes the release app's secrets.
# Passing a secret a reusable workflow does not declare fails the caller.
RELEASE_APP_FROM = (0, 6, 1)
RELEASE_SECRETS = ("    secrets: { RELEASE_APP_ID: '${{ secrets.RELEASE_APP_ID }}', "
                   "RELEASE_APP_KEY: '${{ secrets.RELEASE_APP_KEY }}' }\n")


def takes_app(pubrel: str) -> bool:
    """Whether pubrel `pubrel`'s release workflow takes the app's secrets."""
    parts = []
    for part in pubrel.split("-", 1)[0].split(".")[:3]:
        if not part.isdigit():
            return False
        parts.append(int(part))
    return tuple(parts) >= RELEASE_APP_FROM


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
    if listed and "pub-version" in inputs:
        stop("with gives pub-version, the deprecated form of tools; give tools alone")
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
        jobs[name] = tuple(e for e in EVENTS if e in on and (e in events or e == "queue"))
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
            "site": site(config, root), "tools": listed,
            "targets": rules["targets"] if rules else {}}


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
    in from the workflow that runs deploy-static; None when the repository
    deploys no static site with it."""
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
    return out


def flow(values: dict[str, Any]) -> str:
    """A `with:` mapping on one line: JSON, which YAML reads as it is."""
    return json.dumps(values, ensure_ascii=False)


# The condition each testing job on main runs under: the merge queue did
# not already pass the commit.
UNTESTED = "needs.tested.outputs.tested != 'true'"


def queued(ci: dict[str, Any]) -> str:
    """The event whose workflow the merge queue runs."""
    return "queue" if "queue" in ci["events"] else "pr"


def tested_job(ci: dict[str, Any]) -> str:
    return ("  # Whether the merge queue already passed this commit: then the jobs\n"
            "  # it ran are skipped, and nothing here tests less than the queue did.\n"
            "  tested:\n    name: ci\n"
            f"    uses: {ci['owner']}/pubkit/.github/workflows/job-tested.yml"
            f"@{ref(__version__, ci['commit'])}\n"
            f"    with: {flow({'workflow': path(queued(ci))})}\n"
            "    permissions:\n      actions: read\n      contents: read\n")


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
    # Main takes the merge queue's run of pr.yml where there is one.
    shortcut = event == "main" and "pr" in ci["events"]
    out = [HEADER, f"name: {event}\n\non:\n", trigger(ci, event),
           "\npermissions:\n  contents: read\n\njobs:\n"]
    if shortcut:
        out.append(tested_job(ci))
    for name in names:
        job = table[name]
        given = {**{i: ci["with"][i] for i in job.inputs if i in ci["with"]}, **job.fixed}
        if "required" in job.internal:
            given["required"] = " ".join(ci["targets"][job.standard])
        if "tools" in job.internal and ci["tools"]:
            given["tools"] = json.dumps(ci["tools"])
        if "layout" in job.internal and ci["layout"]:
            given["layout"] = ci["layout"]
        needs = [n for n in job.needs if n in names]
        # On main, a job the queue ran waits to hear whether it passed.
        skips = shortcut and queued(ci) in ci["jobs"][name]
        if skips or (shortcut and name == "sonar"):
            needs = ["tested", *needs]
        first = name == names[0] and not shortcut
        out.append(f"{'' if first else chr(10)}  {name}:\n    name: ci\n")
        if needs:
            out.append(f"    needs: [{', '.join(needs)}]\n")
        scan = shortcut and name == "sonar"
        if scan:
            # The scan runs either way, after the tests or in their place.
            out.append("    if: ${{ !failure() && !cancelled() }}\n")
            given["coverage-run"] = "${{ needs.tested.outputs.run-id }}"
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
    if shortcut:
        out.append(
            "\n  # The one check a ruleset requires: it passes only when every job\n"
            "  # above succeeded or, where the merge queue already passed the\n"
            "  # commit, was skipped for it. None is skipped for not being asked for.\n"
            "  passed:\n    name: ci / passed\n    if: always()\n"
            f"    needs: [{', '.join(['tested', *names])}]\n    runs-on: ubuntu-latest\n"
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
            f"    needs: [{', '.join(names)}]\n    runs-on: ubuntu-latest\n    steps:\n"
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
            f"    with: {flow({'pubrel-version': f'v{pubrel}', **release['with']})}\n"
            + (RELEASE_SECRETS if takes_app(pubrel) else "")
            + "    permissions:\n      contents: write\n")
    return "".join(out)


def site_job(ci: dict[str, Any]) -> str:
    """The pull request's site job: the site built as it is for a deploy,
    and checked by deploy-static at this pubkit, which deploys nothing."""
    s = ci["site"]
    build = s.get("pr_build") or s.get("build")
    given = {"dir": s["check_dir"], "check-only": "true", **({"site-url": s["url"]} if "url" in s else {})}
    return ("\n  # The site as deploy-static checks it before a deploy, so a site that\n"
            "  # would fail to deploy fails the pull request. It deploys nothing.\n"
            "  site:\n    name: ci / site\n    runs-on: ubuntu-latest\n    steps:\n"
            f"      - uses: {CHECKOUT}\n"
            + (f"      - run: {json.dumps(build)}\n" if build else "")
            + f"      - uses: {ci['owner']}/pubkit/actions/deploy-static@{ref(__version__, ci['commit'])}\n"
            f"        with: {flow(given)}\n")


def path(event: str) -> str:
    return f".github/workflows/{event}.yml"


def generated(config: dict[str, Any], root: Path) -> list[tuple[str, str]]:
    """Each workflow pubkit writes, by path: none without a ci block."""
    ci = settings(config, root)
    if ci is None:
        return []
    if "pub-version" in ci["with"]:
        print("pubkit: warning: pubkit.json's ci.with.pub-version is deprecated; list the tool "
              "in ci.tools, with its crate, git and tag", file=sys.stderr)
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
