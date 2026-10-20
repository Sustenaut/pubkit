"""`pubkit supply-chain`: write each named crate's SUPPLY-CHAIN.md from supply-chain/.

For every crate a published crate depends on -- at build time or run
time, on any target -- the report says how its code is vouched for: an
audit imported from an organisation that publishes them, a publisher those
organisations trust, or an exemption, with the evidence recorded in the
exemption's notes. It also marks the dependencies that run code while
compiling (a build script or a procedural macro).

The report is a view of what `cargo vet` enforces, never edited by hand:
`make supply-chain` regenerates it, and `make supply-chain-check` fails in
CI if the committed copy is stale. It goes beside each crate's manifest.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

# The organisations whose audit sets cargo vet imports, by import name.
ORGANISATIONS = {
    "bytecode-alliance": "the Bytecode Alliance",
    "embark-studios": "Embark Studios",
    "google": "Google",
    "isrg": "ISRG",
    "mozilla": "Mozilla",
    "zcash": "Zcash",
}


def load(root, name):
    with open(root / "supply-chain" / name, "rb") as f:
        return tomllib.load(f)


def metadata(root):
    out = subprocess.run(
        ["cargo", "metadata", "--format-version", "1", "--locked"],
        capture_output=True, text=True, check=True, cwd=root).stdout
    return json.loads(out)


def dependencies(root, meta, crate):
    """Every registry crate `crate` needs to build and run, with flags.

    Resolved with `cargo tree -p`, so features are those `crate` itself
    enables: what a user depending on it alone builds, not what other
    workspace members switch on. Every target counts, so a dependency built
    only on some platforms is listed; one that is locked but never built,
    such as an optional dependency nothing enables, is not.
    """
    # The name cargo gets is the workspace's own, never the argument, so it
    # cannot be an option.
    name = next((p["name"] for p in meta["packages"]
                 if p.get("source") is None and p["name"] == crate), None)
    if name is None:
        sys.exit(f"pubkit: {crate!r} is not a crate in this workspace")
    out = subprocess.run(
        ["cargo", "tree", "--locked", "-q", f"--package={name}", "-e", "normal,build", "--target", "all",
         "--prefix", "none", "--format", "{p}"],
        capture_output=True, text=True, check=True, cwd=root).stdout
    wanted = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].startswith("v") and "(/" not in line:
            wanted.add((parts[0], parts[1][1:]))
    kinds = {}
    for pkg in meta["packages"]:
        if pkg.get("source") is not None:
            kinds[(pkg["name"], pkg["version"])] = {
                k for t in pkg["targets"] for k in t["kind"]}
    return sorted(
        ({"name": n, "version": v,
          "build_script": "custom-build" in kinds.get((n, v), set()),
          "proc_macro": "proc-macro" in kinds.get((n, v), set())}
         for n, v in wanted),
        key=lambda d: (d["name"], d["version"]))


def imported_audits(imports, name, version):
    """Organisations whose imported audit covers exactly this version."""
    orgs = []
    for org, data in imports.get("audits", {}).items():
        for audit in data.get("audits", {}).get(name, []):
            if audit.get("version") == version or (
                    audit.get("delta", "").split("->")[-1].strip() == version):
                orgs.append(org)
    return sorted(set(orgs))


def status(dep, config, audits, imports):
    name, version = dep["name"], dep["version"]
    for ex in config.get("exemptions", {}).get(name, []):
        if ex["version"] == version:
            return "not audited (exempt)", ex.get("notes", "")
    for audit in audits.get("audits", {}).get(name, []):
        if audit.get("version") == version or (
                audit.get("delta", "").split("->")[-1].strip() == version):
            return "audited by this project", audit.get("notes", "")
    trusted = audits.get("trusted", {}).get(name, [])
    if trusted:
        publishers = [p for p in imports.get("publisher", {}).get(name, [])
                      if p["version"] == version]
        who = publishers[0]["user-login"] if publishers else "its publisher"
        return f"trusted publisher ({who})", trusted[0].get("notes", "")
    orgs = imported_audits(imports, name, version)
    if orgs:
        return "audited by " + ", ".join(orgs), ""
    return "audited (a chain of imported audits)", ""


def sources(config):
    """The organisations whose audits `config` imports, as a phrase."""
    names = [ORGANISATIONS.get(n, n) for n in config.get("imports", {})]
    if not names:
        return "No audits are imported."
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"Audits are imported from {listed}."


def report(crate, deps, config, audits, imports):
    intro = (
        "Every crate this one needs to build and run, and how its code is vouched for, "
        f"as `cargo vet` enforces it in CI. {sources(config)} A trusted publisher is one "
        "those organisations trust, or this project's own author; the evidence column "
        "says which. *Build-time code* marks a build script (B) or a procedural macro "
        "(P): code that runs while compiling."
    )
    lines = [
        f"# Supply chain: {crate}",
        "",
        "Generated by `pubkit supply-chain` from `supply-chain/`; do not edit.",
        "",
        *textwrap.wrap(intro, 79),
        "",
        "| Crate | Version | Build-time code | Status | Evidence |",
        "|---|---|---|---|---|",
    ]
    counts = {}
    for dep in deps:
        state, notes = status(dep, config, audits, imports)
        key = ("exempt" if state.startswith("not audited")
               else "trusted publisher" if state.startswith("trusted")
               else "audited")
        counts[key] = counts.get(key, 0) + 1
        build = "".join(f for f, on in (("B", dep["build_script"]), ("P", dep["proc_macro"])) if on) or "-"
        lines.append(f"| `{dep['name']}` | {dep['version']} | {build} | {state} | {notes} |")
    summary = ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
    lines[3:3] = [f"{len(deps)} dependencies: {summary}."]
    return "\n".join(lines) + "\n"


def run(root: Path, crates: list[str], check: bool) -> int:
    """Write, or with `check` compare, each crate's SUPPLY-CHAIN.md."""
    if not (root / "supply-chain" / "config.toml").exists():
        print("pubkit: no supply-chain/config.toml here; run `cargo vet init` first",
              file=sys.stderr)
        return 1
    config = load(root, "config.toml")
    audits, imports = load(root, "audits.toml"), load(root, "imports.lock")
    meta = metadata(root)
    dirs = {p["name"]: Path(p["manifest_path"]).parent
            for p in meta["packages"] if p.get("source") is None}
    unknown = [c for c in crates if c not in dirs]
    if unknown:
        print(f"pubkit: no crate here named {', '.join(unknown)}", file=sys.stderr)
        return 1
    stale = []
    for crate in crates:
        text = report(crate, dependencies(root, meta, crate), config, audits, imports)
        path = dirs[crate] / "SUPPLY-CHAIN.md"
        shown = path.relative_to(root)
        if check:
            if not path.exists() or path.read_text() != text:
                stale.append(str(shown))
        else:
            path.write_text(text)
            print(f"wrote {shown}")
    if stale:
        print("pubkit: stale; run `make supply-chain`: " + ", ".join(stale), file=sys.stderr)
        return 1
    if check:
        print("SUPPLY-CHAIN.md current for " + ", ".join(crates))
    return 0
