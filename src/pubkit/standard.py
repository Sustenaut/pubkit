"""The Rust crate standard's checked rules, as data.

Every `[checked: ...]` tag in `plugins/rust-crate-quality/rust-crate-standard.md`
gives a rule a stable ID and names what checks it. `generate` reads the tags
into `standard.json`, which ships inside pubkit; `required` turns that into
the make targets each `rust-ci.yml` job runs for a repository.

    python -m pubkit.standard           # rewrite src/pubkit/standard.json
    python -m pubkit.standard --check   # fail if it differs from the tags
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from importlib import resources
from pathlib import Path

SOURCE = "plugins/rust-crate-quality/rust-crate-standard.md"
GENERATED = "src/pubkit/standard.json"

# Which rust-ci.yml job runs each make target the standard names, in the
# order the job runs them.
JOBS = {
    "gates": ["fmt-check", "lint", "doc", "doc-coverage-check", "publish-check"],
    "functional": ["functional", "vectors-check", "supply-chain-check"],
    "msrv": ["msrv"],
    "fuzz": ["fuzz-smoke"],
    "audit": ["deny", "vet"],
}
# Run for every Rust repository, though no tag names them: formatting, the
# lints and docs section 6's table requires of every kind of crate, and the
# Gherkin suite every package has.
BASELINE = {"fmt-check": "gates", "lint": "gates", "doc": "gates", "functional": "functional"}
WORKFLOW_JOBS = {"test", "cross", "fuzz"}

TAG = re.compile(r"(?<!`)\[checked:\s*(RS-[A-Z]+-\d{2}),\s*([^\]]*)\]")
HEADING = re.compile(r"^## (\d+)\. (.+)$", re.M)


def job_of(target: str) -> str:
    for job, targets in JOBS.items():
        if target in targets:
            return job
    raise ValueError(f"no rust-ci.yml job runs `make {target}`; add it to JOBS")


def parse(text: str) -> list[dict]:
    """The checked rules of the standard's text, in order."""
    headings = [(m.start(), f"{m.group(1)}. {m.group(2)}") for m in HEADING.finditer(text)]
    rules, seen = [], set()
    for m in TAG.finditer(text):
        rule = m.group(1)
        if rule in seen:
            raise ValueError(f"{rule} tags two rules")
        seen.add(rule)
        fields = " ".join(m.group(2).split()).split(";")[0]
        checks, applies = [], "every crate"
        for field in (f.strip() for f in fields.split(",")):
            if field == "library":
                applies = "library"
            elif field.startswith("make "):
                target = field.removeprefix("make ")
                checks.append({"kind": "make", "target": target, "job": job_of(target)})
            elif field.startswith("rust-ci "):
                job = field.removeprefix("rust-ci ")
                if job not in WORKFLOW_JOBS:
                    raise ValueError(f"{rule}: rust-ci.yml has no always-run job `{job}`")
                checks.append({"kind": "job", "job": job})
            elif field in ("pubrel", "pubkit conform"):
                checks.append({"kind": field.split()[-1]})
            else:
                raise ValueError(f"{rule}: unknown check `{field}`")
        if not checks:
            raise ValueError(f"{rule} names no check")
        section = next((h for start, h in reversed(headings) if start < m.start()), "")
        rules.append({"id": rule, "section": section, "applies": applies, "checks": checks})
    return rules


def generate(root: Path) -> str:
    text = (root / SOURCE).read_text(encoding="utf-8")
    data = {
        "comment": f"Generated from {SOURCE} by `make standard`. Do not edit.",
        "source": SOURCE,
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "baseline": [{"target": t, "job": j} for t, j in BASELINE.items()],
        "rules": parse(text),
    }
    return json.dumps(data, indent=2) + "\n"


def load() -> dict:
    """The standard.json this pubkit ships."""
    return json.loads((resources.files("pubkit") / "standard.json").read_text(encoding="utf-8"))


def has_library(root: Path) -> bool:
    """Whether a published package here has a library target."""
    meta = json.loads(subprocess.run(
        ["cargo", "metadata", "--no-deps", "--format-version", "1"],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout)
    for pkg in meta["packages"]:
        if pkg.get("publish") == []:
            continue
        if any("lib" in t["kind"] for t in pkg["targets"]):
            return True
    return False


def required(standard: dict, library: bool, waived: frozenset[str] = frozenset()) -> dict:
    """The make targets each job must run, in order, and the workflow jobs
    the rules turn on.

    A target is required by the baseline, or by any rule that applies and is
    not waived. `waived` holds rule IDs a repository has excepted.
    """
    wanted: set[str] = {b["target"] for b in standard["baseline"]}
    jobs_on = set()
    for rule in standard["rules"]:
        if rule["id"] in waived or (rule["applies"] == "library" and not library):
            continue
        for check in rule["checks"]:
            if check["kind"] == "make":
                wanted.add(check["target"])
            elif check["kind"] == "job":
                jobs_on.add(check["job"])
    out = {job: [t for t in targets if t in wanted] for job, targets in JOBS.items()}
    return {"targets": out, "jobs": sorted(jobs_on)}


# What a change must touch for a pull request to run fuzzing and the
# emulated cross-platform tests: Rust sources, Cargo's files and the pinned
# toolchain, the fuzz targets, and the workflows.
SLOW_JOBS = ("fuzz", "cross")


def touches_code(path: str) -> bool:
    """Whether a change to `path` can change what fuzzing or the cross tests
    run: a Rust source, a Cargo manifest or lock, the toolchain pin, anything
    under fuzz/, or a GitHub workflow."""
    name = path.rsplit("/", 1)[-1]
    return (
        name.endswith(".rs")
        or name in ("Cargo.toml", "Cargo.lock", "rust-toolchain.toml", "rust-toolchain")
        or path.startswith("fuzz/")
        or path.startswith(".github/workflows/")
    )


def skip_slow(event: str, head_ref: str, changed: list[str] | None) -> str | None:
    """Why fuzz and cross can be skipped, or `None` when they must run.

    Only on a pull request, only when the changed files are known, and only
    when none of them touches code. A release pull request (its branch is
    `release/...`), a push, and a scheduled or manual run always run
    everything.
    """
    if event != "pull_request" or changed is None or head_ref.startswith("release/"):
        return None
    if any(touches_code(p) for p in changed):
        return None
    return ("this pull request touches no Rust code, Cargo files, fuzz/ or workflow "
            "files, so fuzz and cross are skipped; releases and the nightly run them")


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parents[2]
    text = generate(root)
    path = root / GENERATED
    if "--check" in argv:
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            print(f"{GENERATED} is stale; run `make standard`", file=sys.stderr)
            return 1
        print(f"{GENERATED} current")
        return 0
    path.write_text(text, encoding="utf-8")
    print(f"wrote {GENERATED}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
