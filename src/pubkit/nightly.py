"""The nightly update of a pub* repository, as two commits' worth of change.

`pubkit pins --pubrel V --pub V` moves every outdated pub* pin to its latest
release: this pubkit (managed files, lock, `rust-ci.yml`), pubrel's release
workflow and `pubrel-version`, the `pub-version` a workflow installs, and
the pub* crates in `Cargo.lock`, within their requirements.

`pubkit crates --report FILE` then tries each upgrade `cargo update` would
make to the other crates, one at a time on top of those it kept, and keeps
one only when `cargo vet` covers it with an audit, imported or by a trusted
publisher, without adding an exemption, and `cargo deny` passes with it.
Each update it drops is listed in the report with the audits cargo vet
recommends for it, as `cargo vet suggest` prints them.

`pubkit nightly` then updates the npm packages within their ranges and
re-locks the Python requirements, when the repository has them. A
repository turns each language's updates off with `update`, and the long
nightly fuzz run with `security.fuzz`, in its pubkit.json (see
`settings`); the pub* pins always come, and the advisory checks every CI
run makes have no switch.

Each prints what it changed, one line per change, for the commit message
and the pull request.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path

from . import __version__
from .paths import confined
from .refs import (ANY_OWNER, AT, PUBKIT_PIN, RELEASE_PIN, VERSION, owner_of, pubrel_commit,
                   release_commit, repin)

# The crates the pub* repositories publish, which `pins` moves rather than
# `crates`.
PUB_CRATES = ("publet-core", "publet-algorithms", "pubunit")

# A row's text, which must not start with '-' (pubrel has no `--`, so it
# would read that as an option) or a space.
ROW = re.compile(r"[^-\s].*", re.S)

# The categories `pubrel add` takes.
CATEGORIES = ("changed", "removed", "added", "fixed", "security", "internal")

# RELEASE_PIN (refs.py) is pubrel's release workflow, by commit or by tag.
PUBREL_VERSION = re.compile(r"(pubrel-version:\s*v)(?P<version>[0-9][^\s,}'\"]*)")
PUB_VERSION = re.compile(r"(pub-version:\s*v)(?P<version>[0-9][^\s,}'\"]*)")
# Every pubkit reusable workflow a repository can call, not only rust-ci.
CI_PIN = re.compile(ANY_OWNER + r"(?P<path>pubkit/\.github/workflows/"
                    r"(?:rust|python|node|service)-ci\.yml@)" + AT + VERSION)

# The languages pubkit's settings name, each by the ecosystem pubkit works
# with: crates, npm packages, and the hash-locked Python requirements.
LANGUAGES = ("rust", "node", "python")

# What `update` and `security.fuzz` default to for each language: all on.
# Fuzzing is cargo-fuzz for Rust, and the property tests (Hypothesis,
# fast-check) for Python and Node; on, a scheduled run fuzzes for longer.
UPDATES = dict.fromkeys(LANGUAGES, True)
FUZZ = dict.fromkeys(LANGUAGES, True)

# The keys `security` takes (security.py reads dependabot and sonar). The
# advisory checks have none: they always run.
SECURITY = ("fuzz", "dependabot", "sonar")


def by_language(name: str, given: object, defaults: dict[str, bool]) -> dict[str, bool]:
    """A per-language switch: an object naming some languages, or one
    boolean for all of them, over the defaults."""
    if isinstance(given, bool):
        return dict.fromkeys(defaults, given)
    if not isinstance(given, dict):
        sys.exit(f"pubkit: pubkit.json's {name} must be an object, such as "
                 '{"rust": true, "python": false}, or true or false for every language')
    unknown = sorted(set(given) - set(defaults))
    if unknown:
        sys.exit(f"pubkit: pubkit.json's {name} has no language {', '.join(unknown)}; "
                 f"it takes {', '.join(defaults)}")
    for key, value in given.items():
        if not isinstance(value, bool):
            sys.exit(f"pubkit: pubkit.json's {name}.{key} must be true or false, not {value!r}")
    return {**defaults, **given}


def settings(root: Path) -> dict[str, dict[str, bool]]:
    """The repository's `update` and `security.fuzz`, per language, from
    pubkit.json over the defaults. Anything else stops the nightly."""
    path = root / "pubkit.json"
    config = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    security = config.get("security", {})
    if not isinstance(security, dict):
        sys.exit("pubkit: pubkit.json's security must be an object, such as "
                 '{"fuzz": {"rust": true}}')
    unknown = sorted(set(security) - set(SECURITY))
    if unknown:
        sys.exit(f"pubkit: pubkit.json's security has no setting {', '.join(unknown)}; "
                 f"it takes {', '.join(SECURITY)} (the advisory checks always run)")
    fuzz = by_language("security.fuzz", security.get("fuzz", {}), FUZZ)
    from .security import dependabot_settings, sonar_settings  # noqa: PLC0415
    dependabot_settings(config)
    sonar_settings(config)
    return {"update": by_language("update", config.get("update", {}), UPDATES), "fuzz": fuzz}


def locked(root: Path) -> dict[str, str]:
    """Crate name -> version, from Cargo.lock (the highest when several)."""
    lock = root / "Cargo.lock"
    if not lock.is_file():
        return {}
    out: dict[str, str] = {}
    for m in re.finditer(r'\[\[package\]\]\nname = "([^"]+)"\nversion = "([^"]+)"',
                         lock.read_text(encoding="utf-8")):
        out[m.group(1)] = m.group(2)
    return out


def cargo(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["cargo", *args], cwd=root, capture_output=True, text=True, check=False)


def refresh_reports(root: Path) -> None:
    """Regenerate each published crate's SUPPLY-CHAIN.md, which names the
    versions in Cargo.lock: an update otherwise leaves it stale."""
    from .conform import packages  # noqa: PLC0415
    from .supply_chain import run as supply_chain  # noqa: PLC0415

    config = json.loads((root / "pubkit.json").read_text(encoding="utf-8"))
    names = [p["name"] for p in packages(root, config)
             if (Path(p["manifest_path"]).parent / "SUPPLY-CHAIN.md").is_file()]
    if names:
        supply_chain(root, names, check=False)


def record(root: Path, text: str, category: str = "internal") -> list[str]:
    """Record `text` for every package `pubrel check` says needs a row for the
    committed change, and return the packages."""
    if category not in CATEGORIES:
        sys.exit(f"pubkit: pubrel has no category {category!r}; use one of "
                 f"{', '.join(CATEGORIES)}")
    row = ROW.fullmatch(text)
    if not row:
        sys.exit(f"pubkit: the row's text {text!r} is empty or starts with a space or "
                 "'-', which pubrel would take for an option")
    text = row.group(0)
    out = subprocess.run(["pubrel", "check", "origin/main"], cwd=root, capture_output=True,
                         text=True, check=False)
    hints = re.findall(r"run `pubrel add( --package ([A-Za-z0-9_][A-Za-z0-9_-]*))? CATEGORY",
                       out.stdout + out.stderr)
    added = []
    for flag, package in hints:
        argv = ["pubrel", "add", *(["--package", package] if flag else []), category, text]
        subprocess.run(argv, cwd=root, check=True, capture_output=True)
        added.append(package or "(the package)")
    return added


def released_by_config(root: Path, pubrel: str, changes: list[str]) -> bool:
    """Move the pubrel that pubkit.json's ci.release names to `pubrel`; True
    when it moved. One the ci block leaves to pubkit moves with pubkit."""
    path = root / "pubkit.json"
    if not path.exists():
        return False
    config = json.loads(path.read_text(encoding="utf-8"))
    release = config.get("ci", {}).get("release") if isinstance(config.get("ci"), dict) else None
    if not isinstance(release, dict) or release.get("pubrel") in (None, pubrel):
        return False
    changes.append(f"pubkit.json: ci.release.pubrel {release['pubrel']} -> {pubrel}")
    release["pubrel"] = pubrel
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return True


def pins(root: Path, pubrel: str, pub: str, lock: bool = True) -> list[str]:
    from .freshness import pinned  # noqa: PLC0415

    changes = []
    before = pinned(root)
    if before and before != __version__:
        from .cli import lock as lock_requirements, sync  # noqa: PLC0415
        sync(root, check=False)
        if lock:
            lock_requirements(root)
        changes.append(f"pubkit {before} -> {__version__}")
    if pubrel and released_by_config(root, pubrel, changes):
        from .cli import sync  # noqa: PLC0415
        sync(root, check=False)
    from .ci import is_generated  # noqa: PLC0415
    calls = owner_of(root)
    for path in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        if is_generated(text):  # pubkit sync writes these, from pubkit.json
            continue
        new = text
        for pattern, version, what in ((PUBKIT_PIN, __version__, "pubkit"),
                                       (RELEASE_PIN, pubrel, "pubrel release workflow"),
                                       (PUBREL_VERSION, pubrel, "pubrel-version"),
                                       (PUB_VERSION, pub, "pub-version")):
            if not version or not pattern.search(new):
                continue
            if pattern is PUBREL_VERSION or pattern is PUB_VERSION:
                commit = None
                moved = pattern.sub(rf"\g<1>{version}", new)
            else:
                # A workflow's pin: the commit moves with the version.
                commit = (pubrel_commit(version, new, calls) if pattern is RELEASE_PIN
                          else release_commit())
                moved = repin(pattern, new, calls, version, commit)
            for m in pattern.finditer(new):
                # "pubkit A -> B" already says that pubkit's own pins moved.
                if m["version"] != version and pattern is not PUBKIT_PIN:
                    changes.append(f"{path.name}: {what} {m['version']} -> {version}")
                elif m["version"] == version and commit and m["commit"] != commit:
                    changes.append(f"{path.name}: {what} {version} pinned to {commit}")
            new = moved
        if new != text:
            path.write_text(new, encoding="utf-8")
    old = locked(root)
    for crate in PUB_CRATES:
        if crate in old:
            cargo(root, "update", "--quiet", "-p", crate)
    new_lock = locked(root)
    changes += [f"{c} {old[c]} -> {new_lock[c]}" for c in PUB_CRATES
                if c in old and new_lock.get(c) != old[c]]
    if new_lock != old:
        refresh_reports(root)
    return changes


UPDATE = re.compile(r"^\s*Updating (\S+) v(\S+) -> v(\S+)", re.M)


def npm_locked(root: Path) -> dict[str, str]:
    """Direct dependency -> version, from package-lock.json."""
    lock = root / "package-lock.json"
    if not lock.is_file():
        return {}
    packages = json.loads(lock.read_text(encoding="utf-8")).get("packages", {})
    top = packages.get("", {})
    names = {**top.get("dependencies", {}), **top.get("devDependencies", {})}
    return {n: packages[f"node_modules/{n}"]["version"] for n in sorted(names)
            if "version" in packages.get(f"node_modules/{n}", {})}


def changed(old: dict[str, str], new: dict[str, str]) -> list[str]:
    """One line per package whose version moved, in name order."""
    return [f"{n} {old[n]} -> {new[n]}" for n in sorted(old) if n in new and new[n] != old[n]]


def npm(root: Path) -> list[str]:
    """Every npm package updated to the newest version its range in
    package.json allows, in package-lock.json only and without running an
    install script. The direct dependencies that moved, one line each."""
    if not (root / "package-lock.json").is_file():
        return []
    old = npm_locked(root)
    subprocess.run(["npm", "update", "--package-lock-only", "--ignore-scripts",
                    "--no-audit", "--no-fund"], cwd=root, check=True, capture_output=True)
    return changed(old, npm_locked(root)) or (["indirect dependencies"]
                                              if git(root, "status", "--porcelain") else [])


def python_locked(root: Path) -> dict[str, str]:
    """Package -> version, from tests/requirements.lock."""
    from .cli import LOCK  # noqa: PLC0415

    lock = root / LOCK
    if not lock.is_file():
        return {}
    return dict(re.findall(r"^([A-Za-z0-9_.-]+)==(\S+)", lock.read_text(encoding="utf-8"), re.M))


def python(root: Path) -> list[str]:
    """tests/requirements.lock re-resolved, so each package moves to the
    newest version requirements.txt allows. The packages that moved."""
    from .cli import LOCK, REQUIREMENTS, lock  # noqa: PLC0415

    if not (root / REQUIREMENTS).is_file() or not (root / LOCK).is_file():
        return []
    old = python_locked(root)
    with contextlib.redirect_stdout(io.StringIO()):
        lock(root)
    return changed(old, python_locked(root))


def candidates(root: Path) -> list[tuple[str, str, str]]:
    """(crate, from, to) for every upgrade `cargo update` would make, the
    pub* crates aside. Downgrades (which the MSRV-aware resolver proposes
    when a locked version needs a newer Rust) are not taken."""
    return upgrades(cargo(root, "update", "--dry-run").stderr)


def upgrades(dry_run: str) -> list[tuple[str, str, str]]:
    """The upgrades in `cargo update --dry-run`'s output."""
    return [(m.group(1), m.group(2), m.group(3)) for m in UPDATE.finditer(dry_run)
            if m.group(1) not in PUB_CRATES]


def recommended(vet_output: str) -> str:
    """What cargo vet recommends auditing, from a failing run's output."""
    lines = [line.rstrip() for line in vet_output.splitlines() if line.strip()]
    at = next((i for i, line in enumerate(lines) if "recommended audits" in line), None)
    return "\n".join(lines[at:] if at is not None else lines[-8:])


def failing(vet_output: str) -> list[str]:
    """The crates `cargo vet` reports missing audits for."""
    return sorted({m.group(1) for m in re.finditer(r"^\s*([A-Za-z0-9_-]+):\S+ missing",
                                                     vet_output, re.M)})


def has(root: Path, crate: str, version: str) -> bool:
    lock = root / "Cargo.lock"
    return lock.is_file() and f'name = "{crate}"\nversion = "{version}"' in lock.read_text(
        encoding="utf-8")


def snapshot(root: Path) -> dict[Path, bytes]:
    """Cargo.lock and supply-chain/, to restore if the updates do not pass."""
    paths = [root / "Cargo.lock", *sorted((root / "supply-chain").glob("*"))]
    return {p: p.read_bytes() for p in paths if p.is_file()}


def restore(saved: dict[Path, bytes]) -> None:
    for path, data in saved.items():
        path.write_bytes(data)


def own_packages(root: Path) -> set[str]:
    meta = json.loads(cargo(root, "metadata", "--no-deps", "--format-version", "1").stdout
                      or '{"packages": []}')
    return {p["name"] for p in meta["packages"]}


def first_party_policy(root: Path, vet_output: str) -> list[str]:
    """When cargo vet stops because the repository's own crates now also
    exist on crates.io, record that they are not audited as crates.io
    crates: `[policy.NAME] audit-as-crates-io = false`. Returns the names."""
    if "audit-as-crates-io" not in vet_output:
        return []
    named = set(re.findall(r"^[ \t]*(?:│[ \t]*)?([A-Za-z0-9_-]+):\S+[ \t]*$", vet_output, re.M))
    own = sorted(named & own_packages(root))
    config = root / "supply-chain" / "config.toml"
    if not own or not config.is_file():
        return []
    text = config.read_text(encoding="utf-8").rstrip("\n")
    for name in own:
        text += (f"\n\n[policy.{name}]\naudit-as-crates-io = false\n"
                 if f"[policy.{name}]" not in text else "")
    config.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
    return own


def tail(proc: subprocess.CompletedProcess, n: int = 8) -> str:
    return "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-n:])


def crates(root: Path, report: Path | None) -> int:
    """Try each upgrade on its own, on top of those already kept, and keep
    it only when `cargo vet` passes without a new exemption and `cargo deny`
    passes. A repository with no Cargo.lock has no crates to update."""
    target = confined(report, "the --report file") if report else None
    lines, skipped = upgrade(root) if (root / "Cargo.lock").is_file() else ([], [])
    for line in lines:
        print(line)
    if target:
        target.write_text(json.dumps({"updated": lines, "skipped": skipped}, indent=2) + "\n",
                          encoding="utf-8")
    return 0


def upgrade(root: Path) -> tuple[list[str], list[dict]]:
    """The crate updates `crates` keeps, and those it skips with why."""
    saved = snapshot(root)
    notes: list[str] = []
    updated: list[tuple[str, str, str]] = []
    skipped: list[dict] = []
    todo = candidates(root)

    vet = cargo(root, "vet")
    own = first_party_policy(root, vet.stdout + vet.stderr) if vet.returncode else []
    if own:
        notes.append("supply-chain/config.toml: " + ", ".join(own) + " (this repository's own) "
                     "not audited as crates.io crates, now that they are published there")
        vet = cargo(root, "vet")
    deny = cargo(root, "deny", "--locked", "check", "--hide-inclusion-graph")
    if vet.returncode or deny.returncode:
        restore(saved)
        why = "main itself does not pass " + ("cargo vet" if vet.returncode else "cargo deny") + \
              ":\n" + tail(vet if vet.returncode else deny)
        skipped = [{"crate": c, "from": a, "to": b, "why": why} for c, a, b in todo]
        todo, notes = [], []

    for crate, old, new in todo:
        if has(root, crate, new):
            # Another kept update brought it along, and vet and deny passed.
            updated.append((crate, old, new))
            continue
        if not has(root, crate, old):
            continue
        before = snapshot(root)
        step = cargo(root, "update", "--quiet", "-p", f"{crate}@{old}", "--precise", new)
        why = ""
        if step.returncode:
            why = "cargo updates it only together with other crates:\n" + tail(step)
        else:
            vet = cargo(root, "vet")
            if vet.returncode:
                why = "not covered by an audit; cargo vet recommends:\n" + recommended(
                    vet.stdout + vet.stderr)
            else:
                # The advisory database was fetched above: --frozen is
                # --locked, offline.
                deny = cargo(root, "deny", "--frozen", "check", "--hide-inclusion-graph")
                if deny.returncode:
                    why = "cargo deny fails with it:\n" + tail(deny)
        if why:
            restore(before)
            skipped.append({"crate": crate, "from": old, "to": new, "why": why})
        else:
            updated.append((crate, old, new))

    if updated and (cargo(root, "vet", "--locked").returncode
                    or cargo(root, "deny", "--locked", "check").returncode):
        # Each passed on its own but not all together: take none.
        restore(saved)
        skipped += [{"crate": c, "from": a, "to": b,
                     "why": "the updates together did not pass cargo vet and cargo deny"}
                    for c, a, b in updated]
        updated, notes = [], []
    if not updated and not notes:
        restore(saved)
    if updated:
        refresh_reports(root)
    return notes + [f"{c} {a} -> {b}" for c, a, b in updated], skipped


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                          check=True).stdout


def commit(root: Path, title: str, lines: list[str]) -> bool:
    """Commit everything changed as `title`, with `lines` as its body and an
    internal pubrel row for each package that needs one. False when nothing
    changed."""
    if not git(root, "status", "--porcelain"):
        return False
    who = ["-c", "user.name=pubkit nightly", "-c", "user.email=noreply@github.com"]
    git(root, "add", "-A")
    git(root, *who, "commit", "-q", "-m", title, "-m", "\n".join(lines))
    if record(root, title):
        git(root, "add", "-A")
        git(root, *who, "commit", "-q", "--amend", "--no-edit")
    return True


def body(pinned: list[str], updated: list[str], skipped: list[dict],
         others: dict[str, list[str]] | None = None, update: bool = True) -> str:
    """The pull request's body. `others` is each further section's lines,
    by heading; with `update` off, the body says the repository takes no
    third-party updates instead of listing them."""
    out = ["Opened by pubkit's nightly workflow, which updates this pull request each night "
           "rather than opening another. Nothing merges it: merge it when CI is green.", ""]
    out += ["## 1. pub* pins", ""] + ([f"- {line}" for line in pinned] or ["Nothing outdated."])
    if not update:
        out += ["", "## Other dependencies", "",
                "None: this repository takes no third-party updates; its pubkit.json sets `\"update\": false`."]
        return "\n".join(out) + "\n"
    number = 2
    if updated or skipped:
        out += ["", f"## {number}. Other crates", "",
                "Updates within their requirements that `cargo vet` covers with an audit "
                "(no new exemption) and `cargo deny` passes.", ""]
        out += [f"- {line}" for line in updated] or ["None."]
        number += 1
    for heading, lines in (others or {}).items():
        if lines:
            out += ["", f"## {number}. {heading}", ""] + [f"- {line}" for line in lines]
            number += 1
    if skipped:
        out += ["", "### Skipped", "",
                "Each needs an audit before the nightly takes it. cargo vet recommends, as `cargo vet suggest` would:", ""]
        for s in skipped:
            out += [f"<details><summary>{s['crate']} {s['from']} -> {s['to']}</summary>", "",
                    "```", s["why"], "```", "", "</details>", ""]
    return "\n".join(out).rstrip("\n") + "\n"


def nightly(root: Path, pubrel: str, pub: str, body_path: Path) -> int:
    """The nightly's commits on the current branch, and the pull request's
    body. Prints the commits it made, in order: `pins` first when it made
    that one, then any of `crates`, `npm` and `python`, or nothing."""
    import tempfile  # noqa: PLC0415

    body_path = confined(body_path, "the --body file")
    update = settings(root)["update"]
    pinned = pins(root, pubrel, pub)
    made = ["pins"] if commit(root, "Move to the latest pub* releases", pinned) else []
    result: dict = {"updated": [], "skipped": []}
    others: dict[str, list[str]] = {}
    if update["rust"]:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "crates.json"
            with contextlib.redirect_stdout(io.StringIO()):
                crates(root, report)
            result = json.loads(report.read_text(encoding="utf-8"))
        if commit(root, "Update other crates the supply-chain audits cover", result["updated"]):
            made.append("crates")
    for language, heading, update_with, title, name in (
            ("node", "npm packages, within their ranges in package.json", npm,
             "Update npm packages within their ranges", "npm"),
            ("python", "Python packages, re-locked from requirements.txt", python,
             "Update the locked Python packages", "python")):
        if update[language]:
            others[heading] = update_with(root)
            if commit(root, title, others[heading]):
                made.append(name)
    body_path.write_text(body(pinned, result["updated"], result["skipped"], others,
                              any(update.values())),
                         encoding="utf-8")
    print(" ".join(made))
    return 0
