"""`pubkit conform`: the checks of the Rust crate standard that no make
target runs, and the repository's declared exceptions.

Each check belongs to a rule the standard tags `pubkit conform`:

- RS-DOC-01: each published library declares a `_documentation` module;
- RS-DOC-03: each published library sets `missing_docs` in its lints;
- RS-SAFE-01: each published crate forbids `unsafe_code`;
- RS-TEST-01: test vectors exist (`testdata/`, or `crates/*/testdata/`);
- RS-FUZZ-01: fuzz targets exist (`fuzz/fuzz_targets/*.rs`);
- RS-SUP-01: `deny.toml` keeps the shared baseline, and
  `supply-chain/config.toml` imports the shared audit sets;
- RS-SUP-04: `SECURITY.md` exists;
- RS-REL-01: `release.json` declares `pubkit audit --check` as a security
  check, so no release is cut without a current quality audit;
- RS-SUP-05: the release key `release.json` names is labelled for what it
  releases (the package's name and "release key"), not with a person's
  name: its label is public in every release it signs.

It also checks, without an exception possible, that every workflow calling
pubkit's `rust-ci.yml`, or the `job-rust-*.yml` jobs a ci block's
generated workflows call, pins the pubkit this repository's lock pins, so the
workflow and the standard it enforces never drift apart.

Exceptions are declared in `pubkit.json`, each with a rule ID, a reason, and
the date by which it will be met or reviewed. An expired exception, or one
naming no rule, fails. `pubkit sync --check` runs all of this for a Rust
repository, so CI runs it on every change.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

from . import __version__, standard
from .refs import AT, VERSION, ref, release_commit

# The audit sets every repository imports (cargo vet).
SHARED_IMPORTS = {
    "bytecode-alliance": "https://raw.githubusercontent.com/bytecodealliance/wasmtime/main/supply-chain/audits.toml",
    "embark-studios": "https://raw.githubusercontent.com/EmbarkStudios/rust-ecosystem/main/audits.toml",
    "google": "https://raw.githubusercontent.com/google/supply-chain/main/audits.toml",
    "isrg": "https://raw.githubusercontent.com/divviup/libprio-rs/main/supply-chain/audits.toml",
    "mozilla": "https://raw.githubusercontent.com/mozilla/supply-chain/main/audits.toml",
    "zcash": "https://raw.githubusercontent.com/zcash/rust-ecosystem/main/supply-chain/audits.toml",
}
# deny.toml settings the baseline fixes: (table, key) -> value.
DENY_BASELINE = {
    ("advisories", "unmaintained"): "all",
    ("advisories", "unsound"): "all",
    ("advisories", "yanked"): "deny",
    ("bans", "wildcards"): "deny",
    ("sources", "unknown-registry"): "deny",
    ("sources", "unknown-git"): "deny",
}
# By commit, with the version in a comment, or by tag (refs.py).
WORKFLOW_PIN = re.compile(
    r"schryer/pubkit/\.github/workflows/(rust-ci|job-rust-[a-z]+)\.yml@" + AT + VERSION)


def today() -> datetime.date:
    """Today, or PUBKIT_TODAY (YYYY-MM-DD), which tests set."""
    fixed = os.environ.get("PUBKIT_TODAY")
    return datetime.date.fromisoformat(fixed) if fixed else datetime.date.today()


def exceptions(config: dict, known: set[str],
               family=lambda rule: not str(rule).startswith("WEB-")) -> tuple[set[str], list[str], list[str]]:
    """The rule IDs waived, a description of each open exception, and the
    problems with the declarations. Only the exceptions whose rule is in
    `family` are read: the Rust crate standard's by default, and the site
    rules' (WEB-) when `pubkit site check` asks."""
    waived, open_, problems = set(), [], []
    for i, e in enumerate(config.get("exceptions", [])):
        where = f"pubkit.json exceptions[{i}]"
        if isinstance(e, dict) and "rule" in e and not family(e["rule"]):
            continue
        if not isinstance(e, dict) or set(e) != {"rule", "reason", "until"}:
            problems.append(f"{where}: give exactly a rule, a reason and an until date")
            continue
        rule, reason, until = e["rule"], str(e["reason"]).strip(), e["until"]
        if rule not in known:
            problems.append(f"{where}: {rule} is not a rule of the standard")
            continue
        if not reason:
            problems.append(f"{where}: {rule} needs a reason")
            continue
        try:
            date = datetime.date.fromisoformat(str(until))
        except ValueError:
            problems.append(f"{where}: {rule}'s until must be a date, YYYY-MM-DD")
            continue
        if date < today():
            problems.append(f"{rule}: the exception expired on {date}; meet the rule, or "
                            f"review the exception and set a new date ({reason})")
            continue
        waived.add(rule)
        open_.append(f"{rule} until {date}: {reason}")
    return waived, open_, problems


def packages(root: Path, config: dict) -> list[dict]:
    """The published packages: pubkit.json's `published`, or else every
    package that does not set `publish = false`."""
    meta = json.loads(subprocess.run(
        ["cargo", "metadata", "--no-deps", "--format-version", "1"],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout)
    names = config.get("published")
    if names is not None:
        return [p for p in meta["packages"] if p["name"] in names]
    return [p for p in meta["packages"] if p.get("publish") != []]


def lints(root: Path, manifest: Path) -> dict:
    """A package's lints, following `[lints] workspace = true`."""
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    table = data.get("lints", {})
    if table.get("workspace") is True:
        workspace = tomllib.loads((root / "Cargo.toml").read_text(encoding="utf-8"))
        return workspace.get("workspace", {}).get("lints", {})
    return table


def level(table: dict, group: str, name: str) -> str | None:
    value = table.get(group, {}).get(name)
    return value.get("level") if isinstance(value, dict) else value


def checks(root: Path, config: dict) -> dict[str, list[str]]:
    """The problems found, by rule ID."""
    found: dict[str, list[str]] = {}

    def problem(rule: str, text: str) -> None:
        found.setdefault(rule, []).append(text)

    for pkg in packages(root, config):
        manifest = Path(pkg["manifest_path"])
        rel = manifest.relative_to(root)
        table = lints(root, manifest)
        if level(table, "rust", "unsafe_code") != "forbid":
            problem("RS-SAFE-01", f"{rel}: set unsafe_code = \"forbid\" in [lints.rust]")
        lib = next((t for t in pkg["targets"] if "lib" in t["kind"]), None)
        if lib is None:
            continue
        if level(table, "rust", "missing_docs") not in ("warn", "deny", "forbid"):
            problem("RS-DOC-03", f"{rel}: set missing_docs in [lints.rust]")
        source = Path(lib["src_path"]).read_text(encoding="utf-8")
        if not re.search(r"^\s*pub mod _documentation\b", source, re.M):
            problem("RS-DOC-01", f"{Path(lib['src_path']).relative_to(root)}: "
                                 f"declare `pub mod _documentation`")
    if not (root / "testdata").is_dir() and not list(root.glob("crates/*/testdata")):
        problem("RS-TEST-01", "no testdata/: the standard asks for test vectors")
    if not list(root.glob("fuzz/fuzz_targets/*.rs")):
        problem("RS-FUZZ-01", "no fuzz/fuzz_targets/*.rs: the standard asks for fuzzing")
    if not (root / "SECURITY.md").is_file():
        problem("RS-SUP-04", "no SECURITY.md")
    for text in release_key_label(root):
        problem("RS-SUP-05", text)
    for text in audit_declared(root):
        problem("RS-REL-01", text)
    deny = root / "deny.toml"
    if not deny.is_file():
        problem("RS-SUP-01", "no deny.toml")
    else:
        data = tomllib.loads(deny.read_text(encoding="utf-8"))
        for (table, key), want in DENY_BASELINE.items():
            if data.get(table, {}).get(key) != want:
                problem("RS-SUP-01", f"deny.toml: set {key} = \"{want}\" in [{table}]")
    vet = root / "supply-chain" / "config.toml"
    if not vet.is_file():
        problem("RS-SUP-01", "no supply-chain/config.toml; run `cargo vet init`")
    else:
        imports = tomllib.loads(vet.read_text(encoding="utf-8")).get("imports", {})
        for name, url in SHARED_IMPORTS.items():
            if imports.get(name, {}).get("url") != url:
                problem("RS-SUP-01", f"supply-chain/config.toml: import {name} from {url}")
    return found


def cbor(data: bytes, i: int = 0):
    """Decode one CBOR item at `i`: (value, next index). Only what key
    objects hold: integers, byte and text strings, arrays and maps."""
    first = data[i]
    major, info = first >> 5, first & 31
    i += 1
    if info < 24:
        n = info
    elif info in (24, 25, 26, 27):
        size = 1 << (info - 24)
        n = int.from_bytes(data[i:i + size], "big")
        i += size
    else:
        raise ValueError("indefinite or reserved length")
    if major == 0:
        return n, i
    if major == 1:
        return -1 - n, i
    if major in (2, 3):
        raw = data[i:i + n]
        return (raw if major == 2 else raw.decode()), i + n
    if major == 4:
        items = []
        for _ in range(n):
            item, i = cbor(data, i)
            items.append(item)
        return items, i
    if major == 5:
        out = {}
        for _ in range(n):
            key, i = cbor(data, i)
            out[key], i = cbor(data, i)
        return out, i
    raise ValueError(f"CBOR major type {major}")


def release_key_label(root: Path) -> list[str]:
    """Problems with the label of the key release.json names (RS-SUP-05)."""
    path = root / "release.json"
    if not path.is_file():
        return ["no release.json: the release key cannot be checked"]
    release = json.loads(path.read_text(encoding="utf-8"))
    key = release.get("key")
    if not key:
        return ["release.json names no key"]
    names = [release.get("name")] + [p.get("name") for p in release.get("packages", [])]
    names = [n for n in names if n]
    # A workspace's crates share a family name: publet-core, publet-cli.
    names += [n.split("-")[0] for n in names]
    obj = root / release.get("corpus", "corpus") / "objects" / (key.replace(":", "_") + ".cbor")
    if not obj.is_file():
        return [f"the key object {key} is not in {obj.parent.relative_to(root)}/: export it"]
    try:
        label = cbor(obj.read_bytes())[0].get("body", {}).get("label", "")
    except (ValueError, IndexError, UnicodeDecodeError, AttributeError):
        return [f"{obj.relative_to(root)} is not a key object"]
    if not label.endswith("release key") or not any(n in label for n in names):
        want = f"{min(names, key=len)} release key" if names else "<package> release key"
        return [f"the release key is labelled {label!r}: label it for what it releases, "
                f"such as {want!r}, not with a person's name; rotate to a key labelled so"]
    return []


def audit_declared(root: Path) -> list[str]:
    """Problems with release.json's declaration of the audit check (RS-REL-01)."""
    path = root / "release.json"
    if not path.is_file():
        return []  # RS-SUP-05 already reports a missing release.json
    release = json.loads(path.read_text(encoding="utf-8"))
    checks = release.get("security", {}).get("checks", [])
    if any("pubkit audit --check" in str(c.get("run", "")) for c in checks):
        return []
    return ["release.json declares no `pubkit audit --check` security check: add one "
            "named \"quality audit current\", whose run is `.venv/bin/pubkit audit --check` "
            "and whose version is `.venv/bin/pubkit --version`"]


def workflow_pins(root: Path) -> list[str]:
    """Problems with the pins of rust-ci.yml, or of the job-rust-*.yml jobs
    the ci block's workflows call, in this repository's workflows."""
    problems, uses = [], 0
    commit = release_commit()
    for path in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        for m in WORKFLOW_PIN.finditer(path.read_text(encoding="utf-8")):
            uses += 1
            if m["version"] != __version__:
                problems.append(f"{path.relative_to(root)}: {m.group(1)}.yml@v{m['version']} is "
                                f"not pubkit {__version__}, which tests/requirements.lock pins; "
                                f"use @{ref(__version__, commit)}")
            elif commit and m["commit"] and m["commit"] != commit:
                problems.append(f"{path.relative_to(root)}: {m.group(1)}.yml@{m['commit']} is "
                                f"not the commit of pubkit {__version__}'s tag; use "
                                f"@{ref(__version__, commit)}")
    if not uses:
        problems.append("no workflow calls schryer/pubkit/.github/workflows/rust-ci.yml; "
                        "give pubkit.json a ci block, and `pubkit sync` writes them")
    return problems


def run(root: Path, config: dict) -> tuple[list[str], list[str]]:
    """(lines to print, problems). An empty problem list means the
    repository conforms."""
    rules = standard.load()["rules"]
    known = {r["id"] for r in rules}
    waived, open_, problems = exceptions(config, known)
    for rule, texts in sorted(checks(root, config).items()):
        if rule in waived:
            continue
        problems += [f"{rule}: {t}" for t in texts]
    problems += workflow_pins(root)
    conformed = sum(1 for r in rules if any(c["kind"] == "conform" for c in r["checks"]))
    lines = [f"conforms to the Rust crate standard ({conformed} rules checked here)"]
    lines += [f"open exception: {e}" for e in open_]
    return lines, problems
