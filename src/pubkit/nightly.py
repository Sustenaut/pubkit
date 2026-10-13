"""The nightly update of a pub* repository, as two commits' worth of change.

`pubkit pins --pubrel V --pub V` moves every outdated pub* pin to its latest
release: this pubkit (managed files, lock, `rust-ci.yml`), pubrel's release
workflow and `pubrel-version`, the `pub-version` a workflow installs, and
the pub* crates in `Cargo.lock`, within their requirements.

`pubkit crates --report FILE` then updates the other crates in `Cargo.lock`
within their requirements, and keeps an update only when `cargo vet`
covers it with an audit, imported or by a trusted publisher, without adding
an exemption, and `cargo deny` passes with it. Each update it drops is
listed in the report with what `cargo vet suggest` says it needs.

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
MAX_ROUNDS = 20

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


def suggestions(root: Path, crates: list[str]) -> dict[str, str]:
    """What `cargo vet suggest` says each of `crates` needs: its lines."""
    out = cargo(root, "vet", "suggest").stdout
    found: dict[str, list[str]] = {}
    for line in out.splitlines():
        for crate in crates:
            if re.search(rf"\b{re.escape(crate)}\b", line):
                found.setdefault(crate, []).append(line.rstrip())
    return {c: "\n".join(found.get(c, [])) or "(cargo vet suggest named nothing for it)"
            for c in crates}


def failing(vet_output: str) -> list[str]:
    """The crates `cargo vet` reports missing audits for."""
    return sorted({m.group(1) for m in re.finditer(r"^\s*([A-Za-z0-9_-]+):\S+ missing",
                                                     vet_output, re.M)})


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


def crates(root: Path, report: Path | None) -> int:
    saved = snapshot(root)
    old = locked(root)
    cargo(root, "update", "--quiet")
    new = locked(root)
    changed = {c: (old[c], new[c]) for c in new
               if c in old and new[c] != old[c] and c not in PUB_CRATES}
    skipped: dict[str, str] = {}
    notes: list[str] = []
    for _ in range(MAX_ROUNDS):
        vet = cargo(root, "vet")
        if vet.returncode == 0:
            break
        own = first_party_policy(root, vet.stdout + vet.stderr)
        if own:
            notes.append("supply-chain/config.toml: " + ", ".join(own) + " (this repository's own) "
                         "not audited as crates.io crates, now that they are published there")
            continue
        names = [c for c in failing(vet.stdout + vet.stderr) if c in changed and c not in skipped]
        if not names:
            # cargo vet failed for another reason: update nothing, and say why.
            reason = "\n".join((vet.stdout + vet.stderr).strip().splitlines()[-8:])
            restore(saved)
            for name in changed:
                skipped.setdefault(name, "cargo vet failed for a reason no update caused:\n"
                                   + reason)
            break
        why = suggestions(root, names)
        for name in names:
            cargo(root, "update", "--quiet", "-p", f"{name}@{changed[name][1]}",
                  "--precise", changed[name][0])
            skipped[name] = "not covered by an audit:\n" + why[name]
    else:
        for name in changed:
            skipped.setdefault(name, "cargo vet did not pass within the rounds allowed")
    if cargo(root, "vet", "--locked").returncode != 0 or \
            cargo(root, "deny", "--locked", "check").returncode != 0:
        # Conservative: when the result does not pass both, update nothing.
        restore(saved)
        for name in changed:
            skipped.setdefault(name, "the updates together did not pass cargo vet and cargo deny")
    final = locked(root)
    if final != old:
        refresh_reports(root)
    updated = {c: v for c, v in changed.items() if final.get(c) == v[1]}
    lines = notes + [f"{c} {a} -> {b}" for c, (a, b) in sorted(updated.items())]
    for line in lines:
        print(line)
    if report:
        report.write_text(json.dumps({
            "updated": lines,
            "skipped": [{"crate": c, "from": changed[c][0], "to": changed[c][1], "why": w}
                        for c, w in sorted(skipped.items())],
        }, indent=2) + "\n", encoding="utf-8")
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
                "Each needs an audit before the nightly takes it; `cargo vet suggest` says:", ""]
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
