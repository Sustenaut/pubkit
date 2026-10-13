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

Each prints what it changed, one line per change, for the commit message
and the pull request.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import __version__

# The crates the pub* repositories publish, which `pins` moves rather than
# `crates`.
PUB_CRATES = ("publet-core", "publet-algorithms", "pubunit")

RELEASE_PIN = re.compile(r"(schryer/pubrel/\.github/workflows/release\.yml@v)([0-9][^\s'\"]*)")
PUBREL_VERSION = re.compile(r"(pubrel-version:\s*v)([0-9][^\s,}'\"]*)")
PUB_VERSION = re.compile(r"(pub-version:\s*v)([0-9][^\s,}'\"]*)")


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
    out = subprocess.run(["pubrel", "check", "origin/main"], cwd=root, capture_output=True,
                         text=True, check=False)
    hints = re.findall(r"run `pubrel add( --package ([A-Za-z0-9_-]+))? CATEGORY",
                       out.stdout + out.stderr)
    added = []
    for flag, package in hints:
        argv = ["pubrel", "add", *(["--package", package] if flag else []), category, text]
        subprocess.run(argv, cwd=root, check=True, capture_output=True)
        added.append(package or "(the package)")
    return added


def pins(root: Path, pubrel: str, pub: str, lock: bool = True) -> list[str]:
    from .freshness import WORKFLOW_PIN, pinned  # noqa: PLC0415

    changes = []
    before = pinned(root)
    if before and before != __version__:
        from .cli import lock as lock_requirements, sync  # noqa: PLC0415
        sync(root, check=False)
        if lock:
            lock_requirements(root)
        changes.append(f"pubkit {before} -> {__version__}")
    for path in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        new = WORKFLOW_PIN.sub(rf"\g<1>{__version__}", text)
        for pattern, version, what in ((RELEASE_PIN, pubrel, "pubrel release workflow"),
                                       (PUBREL_VERSION, pubrel, "pubrel-version"),
                                       (PUB_VERSION, pub, "pub-version")):
            if not version:
                continue
            for m in pattern.finditer(new):
                if m.group(2) != version:
                    changes.append(f"{path.name}: {what} {m.group(2)} -> {version}")
            new = pattern.sub(rf"\g<1>{version}", new)
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
    named = set(re.findall(r"^\s*│?\s*([A-Za-z0-9_-]+):\S+\s*$", vet_output, re.M))
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
    passes."""
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
    lines = notes + [f"{c} {a} -> {b}" for c, a, b in updated]
    for line in lines:
        print(line)
    if report:
        report.write_text(json.dumps({"updated": lines, "skipped": skipped}, indent=2) + "\n",
                          encoding="utf-8")
    return 0


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


def body(pinned: list[str], updated: list[str], skipped: list[dict]) -> str:
    out = ["Opened by pubkit's nightly workflow, which updates this pull request each night "
           "rather than opening another. Nothing merges it: merge it when CI is green.", ""]
    out += ["## 1. pub* pins", ""] + ([f"- {line}" for line in pinned] or ["Nothing outdated."])
    out += ["", "## 2. Other crates", "",
            "Updates within their requirements that `cargo vet` covers with an audit "
            "(no new exemption) and `cargo deny` passes.", ""]
    out += [f"- {line}" for line in updated] or ["None."]
    if skipped:
        out += ["", "### Skipped", "",
                "Each needs an audit before the nightly takes it. cargo vet recommends, as `cargo vet suggest` would:", ""]
        for s in skipped:
            out += [f"<details><summary>{s['crate']} {s['from']} -> {s['to']}</summary>", "",
                    "```", s["why"], "```", "", "</details>", ""]
    return "\n".join(out).rstrip("\n") + "\n"


def nightly(root: Path, pubrel: str, pub: str, body_path: Path) -> int:
    """Both commits on the current branch, and the pull request's body.
    Prints the commits it made: `pins`, `crates`, both, or nothing."""
    import contextlib  # noqa: PLC0415
    import io  # noqa: PLC0415

    pinned = pins(root, pubrel, pub)
    made = ["pins"] if commit(root, "Move to the latest pub* releases", pinned) else []
    report = root.parent / f"{root.name}-crates.json"
    with contextlib.redirect_stdout(io.StringIO()):
        crates(root, report)
    result = json.loads(report.read_text(encoding="utf-8"))
    report.unlink()
    if commit(root, "Update other crates the supply-chain audits cover", result["updated"]):
        made.append("crates")
    body_path.write_text(body(pinned, result["updated"], result["skipped"]), encoding="utf-8")
    print(" ".join(made))
    return 0
