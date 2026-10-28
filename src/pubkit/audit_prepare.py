"""`pubkit audit prepare`: a release's quality audit, with every rule a
machine can decide already decided.

    pubkit audit prepare --package NAME [--version V] [--out FILE] [--quick] [--force]

It writes `audit/<package>-<version>.md` in the shape the rust-crate-quality
agent writes it, for the release `pubrel next --package NAME` gives:

- the header `pubkit audit --check` reads, with the commit audited and the
  release it follows;
- every machine-decidable rule decided, each finding under its severity
  with its file and line: the docs and lint warnings, public functions with
  no example or no `# Errors`, hidden setup lines in examples, the
  "Related crates" versions against `Cargo.lock`, the claims `SECURITY.md`
  makes against what the workflows run, the packaging metadata, and the
  checks the standard's tags name;
- every rule that needs judgement under "Needs review", marked
  `[needs review]`, with what the machine found for it.

`pubkit audit --check` refuses a report while any mark remains, so a release
needs a person (helped, perhaps, by `pubkit audit draft`) to judge those
rules. `--quick` skips what takes minutes: the builds as docs.rs and the
minimum Rust version make them, `cargo publish --dry-run`, and the make
targets.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__, standard
from .audit import DIR, current
from .doc_coverage import COVERS, impl_of, strip

REVIEW = "**[needs review]**"
SEVERITIES = (("blocks", "Blocks a release"), ("short", "Falls short of the standard"),
              ("consider", "Worth considering"))
FN = re.compile(r"\s*pub (?:const |async |unsafe )*fn (\w+)")
TYPE = re.compile(r"\s*pub (struct|enum|trait|type) (\w+)")
VERSION = re.compile(r"\b(\d+\.\d+(?:\.\d+)?)\b")


@dataclass
class Finding:
    severity: str  # blocks, short or consider
    rule: str      # "§2 `# Errors`", "RS-DOC-04"
    where: str     # "src/lib.rs:12", or "" when no line is meant
    text: str


@dataclass
class Judged:
    """A judged rule: met, not applicable, or for a person to review."""
    rule: dict
    status: str = "review"  # met, n/a or review
    evidence: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class Check:
    name: str
    result: str


class Audit:
    """What the audit found, as it goes."""

    def __init__(self):
        self.findings: list[Finding] = []
        self.checks: list[Check] = []
        self.not_run: list[Check] = []
        self.judged: dict[str, Judged] = {}
        self.met: list[str] = []

    def find(self, severity: str, rule: str, where: str, text: str, judged: str = ""):
        """Record a finding. `judged` names a judged rule the finding
        settles, such as a section that is not there: nothing is left there
        for a person to judge."""
        self.findings.append(Finding(severity, rule, where, text))
        if judged and judged in self.judged and self.judged[judged].status == "review":
            self.judged[judged].status = "decided"

    def evidence(self, key: str, line: str):
        if key in self.judged:
            self.judged[key].evidence.append(line)

    def decide(self, key: str, met_line: str = ""):
        """A judged rule the machine decides in full: met, unless a finding
        was recorded against it."""
        j = self.judged.get(key)
        if j is None or j.status == "n/a":
            return
        if any(f.rule.startswith(f"§{key} ") for f in self.findings):
            j.status = "decided"
        else:
            j.status = "met"
            if met_line:
                self.met.append(met_line)


# --- the package ---------------------------------------------------------------


def run(args: list[str], cwd: Path, env: dict | None = None, timeout: int | None = None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False,
                          env={**os.environ, **(env or {})}, timeout=timeout)


class Package:
    """The package being released, and the repository around it."""

    def __init__(self, root: Path, name: str, version: str | None):
        self.root = root
        self.name = name
        meta = run(["cargo", "metadata", "--format-version", "1", "--no-deps"], root)
        if meta.returncode:
            sys.exit(f"pubkit: cargo metadata failed: {meta.stderr.strip()}")
        self.meta = json.loads(meta.stdout)
        pkg = next((p for p in self.meta["packages"]
                    if p["name"] == name and p["id"] in self.meta["workspace_members"]), None)
        if pkg is None:
            sys.exit(f"pubkit: no package here named {name}")
        self.pkg = pkg
        self.manifest = Path(pkg["manifest_path"])
        self.dir = self.manifest.parent
        self.targets = pkg["targets"]
        self.lib = next((Path(t["src_path"]) for t in self.targets if "lib" in t["kind"]), None)
        self.bins = [t for t in self.targets if "bin" in t["kind"]]
        self.toml = tomllib.loads(self.manifest.read_text(encoding="utf-8"))
        workspace = root / "Cargo.toml"
        self.workspace = tomllib.loads(workspace.read_text(encoding="utf-8")) if workspace.is_file() else {}
        self.release = self._release_entry()
        self.version = version or self._next_version()
        self.tag_prefix = self._tag_prefix()
        self.last_tag = self._last_tag()
        head = run(["git", "rev-parse", "HEAD"], root)
        self.commit = head.stdout.strip() if head.returncode == 0 else ""
        self.dirty = bool(run(["git", "status", "--porcelain"], root).stdout.strip())
        self.config = self._pubkit_config()

    @property
    def library(self) -> bool:
        return self.lib is not None

    def rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    def _release_entry(self) -> dict:
        path = self.root / "release.json"
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("packages") or [data]
        entry = next((e for e in entries if e.get("name") == self.name), None)
        if entry is None:
            return {}
        return {**{k: v for k, v in data.items() if k != "packages"}, **entry,
                "_listed": "packages" in data}

    def _next_version(self) -> str:
        pubrel = os.environ.get("PUBREL", "pubrel")
        if shutil.which(pubrel) is None:
            sys.exit("pubkit: audit prepare needs pubrel for the version being released "
                     "(`pubrel next --package NAME`); install it, or give --version")
        out = run([pubrel, "next", "--package", self.name], self.root)
        version = out.stdout.strip().split()[-1] if out.stdout.strip() else ""
        if out.returncode or not re.fullmatch(r"\d+\.\d+\.\d+", version):
            sys.exit(f"pubkit: `pubrel next --package {self.name}` gives no version to release: "
                     f"{(out.stderr or out.stdout).strip()}")
        return version

    def _tag_prefix(self) -> str:
        """The tag prefix pubrel gives this package's releases."""
        if self.release.get("tag-prefix"):
            return self.release["tag-prefix"]
        manifest = Path(self.release.get("manifest", {}).get("path", "Cargo.toml"))
        if self.release.get("_listed") and str(manifest.parent) not in ("", "."):
            return f"{self.name}-v"
        return "v"

    def _last_tag(self) -> str:
        out = run(["git", "tag", "-l", f"{self.tag_prefix}[0-9]*", "--sort=-v:refname"], self.root)
        tags = [t for t in out.stdout.split() if re.fullmatch(re.escape(self.tag_prefix) + r"\d+\.\d+\.\d+", t)]
        return tags[0] if tags else ""

    def _pubkit_config(self) -> dict:
        path = self.root / "pubkit.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def field(self, key: str):
        """A `[package]` field, following `key.workspace = true`."""
        value = self.toml.get("package", {}).get(key)
        if isinstance(value, dict) and value.get("workspace"):
            return self.workspace.get("workspace", {}).get("package", {}).get(key)
        return value

    def sources(self) -> list[Path]:
        """The package's Rust sources, not its tests or other members'."""
        src = (self.lib or (Path(self.bins[0]["src_path"]) if self.bins else self.dir / "src")).parent
        others = [Path(p["manifest_path"]).parent for p in self.meta["packages"]
                  if p["id"] in self.meta["workspace_members"] and p["name"] != self.name]
        return sorted(p for p in src.rglob("*.rs")
                      if not any(o != self.dir and o in p.parents for o in others))

    def readme(self) -> Path | None:
        named = self.pkg.get("readme")
        for candidate in ([Path(named)] if named else []) + [self.dir / "README.md", self.root / "README.md"]:
            path = candidate if candidate.is_absolute() else self.dir / candidate
            if path.is_file():
                return path
        return None

    def lock_versions(self) -> dict[str, set[str]]:
        path = self.root / "Cargo.lock"
        if not path.is_file():
            return {}
        out: dict[str, set[str]] = {}
        for p in tomllib.loads(path.read_text(encoding="utf-8")).get("package", []):
            out.setdefault(p["name"], set()).add(p["version"])
        return out


# --- reading Rust --------------------------------------------------------------


@dataclass
class Item:
    name: str
    kind: str
    path: Path
    line: int
    docs: list[tuple[int, str]]  # (line, text without the `///`)
    attrs: list[str]
    signature: str
    body: str


def items(paths: list[Path]) -> list[Item]:
    """Public items with their docs, attributes, signature and body, as
    `pubkit doc-coverage` finds them."""
    out = []
    for path in paths:
        lines = path.read_text(encoding="utf-8").splitlines()
        depth, impls, in_test = 0, [], None
        for i, line in enumerate(lines):
            if in_test is None and re.match(r"\s*#\[cfg\(test\)\]", line):
                in_test = depth
            code = strip(line)
            if in_test is None:
                impl = impl_of(line) if line.strip().startswith("impl") else None
                fn, ty = FN.match(line), TYPE.match(line)
                if fn or ty:
                    docs, attrs, j = [], [], i - 1
                    while j >= 0 and (lines[j].strip().startswith("///") or lines[j].strip().startswith("#[")):
                        text = lines[j].strip()
                        if text.startswith("///"):
                            docs.append((j + 1, text[3:].removeprefix(" ")))
                        else:
                            attrs.append(text)
                        j -= 1
                    docs.reverse()
                    sig, k = [], i
                    while k < len(lines) and k < i + 20:
                        sig.append(lines[k])
                        if "{" in strip(lines[k]) or strip(lines[k]).rstrip().endswith(";"):
                            break
                        k += 1
                    body = []
                    if fn and "{" in strip(lines[k] if k < len(lines) else ""):
                        level = 0
                        for m in range(k, min(len(lines), k + 400)):
                            body.append(lines[m])
                            level += strip(lines[m]).count("{") - strip(lines[m]).count("}")
                            if level <= 0 and m > k or (level <= 0 and "}" in strip(lines[m])):
                                break
                    if fn:
                        owner = impls[-1][1] if impls else ""
                        name = f"{owner}::{fn.group(1)}" if owner else fn.group(1)
                        out.append(Item(name, "fn", path, i + 1, docs, attrs, " ".join(s.strip() for s in sig), "\n".join(body)))
                    else:
                        out.append(Item(ty.group(2), ty.group(1), path, i + 1, docs, attrs, " ".join(s.strip() for s in sig), ""))
                if impl and "{" in code:
                    impls.append((depth, impl))
            depth += code.count("{") - code.count("}")
            while impls and depth <= impls[-1][0]:
                impls.pop()
            if in_test is not None and depth <= in_test and "}" in code:
                in_test = None
    return out


NOT_RUST = {"text", "json", "sh", "bash", "console", "toml", "yaml", "plain", "md", "markdown", "cbor"}


def examples(docs: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    """The Rust examples in an item's docs: each the lines between its fences."""
    out, current, fence = [], None, None
    for line, text in docs:
        stripped = text.strip()
        if current is None and stripped.startswith("```"):
            tags = {t.strip() for t in stripped[3:].split(",") if t.strip()}
            fence = not (tags & NOT_RUST)
            current = []
        elif current is not None and stripped.startswith("```"):
            if fence:
                out.append(current)
            current = None
        elif current is not None:
            current.append((line, text))
    return out


def hidden(text: str) -> bool:
    return text.strip() == "#" or text.strip().startswith("# ")


def plumbing(text: str) -> bool:
    """A hidden line that only carries the error plumbing or an import."""
    t = text.strip()[1:].strip()
    return (not t or t.startswith(("Ok::<", "Ok(", "fn main", "}", "use ", "#!", "#[", "extern crate"))
            or t in ("{",))


def headings(doc: list[tuple[int, str]]) -> list[tuple[int, int, str]]:
    """Markdown headings outside code: (line, level, text)."""
    out, code = [], False
    for line, text in doc:
        if text.strip().startswith("```"):
            code = not code
            continue
        m = re.match(r"(#{1,6})\s+(.+)", text)
        if m and not code:
            out.append((line, len(m.group(1)), m.group(2).strip()))
    return out


def crate_root_docs(lib: Path) -> list[tuple[int, str]]:
    """The crate root's docs: its `//!` lines or its `/*! ... */` block."""
    lines = lib.read_text(encoding="utf-8").splitlines()
    out, block = [], False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if block:
            if "*/" in stripped:
                out.append((i + 1, line.split("*/", 1)[0]))
                block = False
                continue
            out.append((i + 1, line))
        elif stripped.startswith("/*!"):
            rest = stripped[3:]
            if "*/" in rest:
                out.append((i + 1, rest.split("*/", 1)[0]))
            else:
                block = True
                if rest.strip():
                    out.append((i + 1, rest))
        elif stripped.startswith("//!"):
            out.append((i + 1, stripped[3:].removeprefix(" ")))
    return out


def markdown(path: Path) -> list[tuple[int, str]]:
    return list(enumerate(path.read_text(encoding="utf-8").splitlines(), start=1))


def section_of(doc: list[tuple[int, str]], title: re.Pattern) -> list[tuple[int, str]]:
    """The lines under the first heading matching `title`, to the next
    heading of the same level or higher."""
    heads = headings(doc)
    start = next(((line, level) for line, level, text in heads if title.search(text)), None)
    if start is None:
        return []
    end = next((line for line, level, _ in heads if line > start[0] and level <= start[1]), None)
    return [(n, t) for n, t in doc if start[0] < n and (end is None or n < end)]


# --- the checks ----------------------------------------------------------------


def cargo_messages(pkg: Package, args: list[str], env: dict | None = None):
    """(exit code, this package's warnings and errors as (level, where, text))."""
    proc = run(["cargo", *args, "-p", pkg.name, "--message-format=json"], pkg.root, env=env)
    out = []
    for line in proc.stdout.splitlines():
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("reason") != "compiler-message" or msg.get("package_id") != pkg.pkg["id"]:
            continue
        m = msg["message"]
        if m.get("level") not in ("warning", "error") or not m.get("spans"):
            continue
        span = next((s for s in m["spans"] if s.get("is_primary")), m["spans"][0])
        where = f"{pkg.rel(pkg.root / span['file_name'])}:{span['line_start']}"
        if (where, m["message"]) not in [(w, t) for _, w, t in out]:
            out.append((m["level"], where, m["message"]))
    return proc, out


def check_builds(pkg: Package, audit: Audit):
    """cargo doc and clippy for this package, each warning with its place."""
    proc, msgs = cargo_messages(pkg, ["doc", "--no-deps"])
    for level, where, text in msgs:
        audit.find("blocks", "RS-DOC-01, `make doc`", where, f"rustdoc {level}: {text}")
    audit.checks.append(Check(f"`cargo doc -p {pkg.name} --no-deps`",
                              "pass" if proc.returncode == 0 and not msgs else
                              f"{len(msgs)} warning(s) or error(s)" if msgs else "fails"))
    proc, msgs = cargo_messages(pkg, ["clippy", "--all-targets"])
    for level, where, text in msgs:
        audit.find("blocks", "RS-DOC-03, `make lint`", where, f"clippy {level}: {text}")
    audit.checks.append(Check(f"`cargo clippy -p {pkg.name} --all-targets`",
                              "pass" if proc.returncode == 0 and not msgs else
                              f"{len(msgs)} warning(s) or error(s)" if msgs else "fails"))
    if pkg.library:
        proc = run(["cargo", "test", "--doc", "-p", pkg.name], pkg.root)
        passed = re.findall(r"test result: ok\. (\d+) passed", proc.stdout)
        if proc.returncode:
            audit.find("blocks", "§2 examples", "", f"`cargo test --doc -p {pkg.name}` fails: "
                       f"{tail(proc.stdout + proc.stderr)}")
        audit.checks.append(Check(f"`cargo test --doc -p {pkg.name}`",
                                  f"pass ({sum(map(int, passed))} doctests)" if proc.returncode == 0 else "fails"))
    else:
        proc, msgs = cargo_messages(pkg, ["clippy", "--", "-W", "clippy::missing_docs_in_private_items"])
        undocumented = [m for m in msgs if "missing documentation" in m[2]]
        for _, where, text in undocumented[:10]:
            audit.evidence("9.2", f"`{where}`: {text}")
        audit.checks.append(Check(f"`cargo clippy -p {pkg.name} -- -W clippy::missing_docs_in_private_items`",
                                  f"{len(undocumented)} undocumented private item(s)"))
        if undocumented:
            audit.find("consider", "§9.2 module docs", "",
                       f"{len(undocumented)} private items have no docs "
                       f"(`clippy::missing_docs_in_private_items`); the first are under Needs review")
        check_help(pkg, audit)


def check_help(pkg: Package, audit: Audit):
    """A tool's `--help` exits 0 and prints to stdout (§9)."""
    build = run(["cargo", "build", "-p", pkg.name, "--bins"], pkg.root)
    if build.returncode:
        audit.not_run.append(Check("`--help`", "the binaries do not build"))
        return
    target = Path(pkg.meta["target_directory"])
    for b in pkg.bins:
        exe = target / "debug" / b["name"]
        proc = run([str(exe), "--help"], pkg.root, timeout=60)
        if proc.returncode != 0 or not proc.stdout.strip() or proc.stderr.strip():
            audit.find("short", "§9.5 usage text", "",
                       f"`{b['name']} --help` exits {proc.returncode}, with "
                       f"{'nothing' if not proc.stdout.strip() else 'its text'} on stdout"
                       f"{' and output on stderr' if proc.stderr.strip() else ''}; the standard asks "
                       f"that it exit 0 and print to stdout", judged="9.5")
        else:
            audit.evidence("9.5", f"`{b['name']} --help` exits 0 and prints "
                                  f"{len(proc.stdout.splitlines())} lines to stdout, nothing to stderr")
        audit.checks.append(Check(f"`{b['name']} --help`", f"exit {proc.returncode}"))


def tail(text: str, lines: int = 3) -> str:
    kept = [line.strip() for line in text.strip().splitlines() if line.strip()][-lines:]
    return " / ".join(kept)[:400]


def check_items(pkg: Package, audit: Audit):
    """§2: examples, `# Errors`, `# Panics`, hidden setup, error types, usize."""
    if not pkg.library:
        return
    found = items(pkg.sources())
    unwrapping, hidden_setup, setups = 0, 0, []
    for it in found:
        where = f"{pkg.rel(it.path)}:{it.line}"
        text = "\n".join(t for _, t in it.docs)
        exs = examples(it.docs)
        if it.kind == "fn" and not exs:
            audit.find("blocks", "RS-DOC-04, §2", where, f"`{it.name}` has no example")
        if it.kind == "fn" and re.search(r"->\s*(?:[\w:]+::)?Result\b", it.signature) and "# Errors" not in text:
            audit.find("short", "§2.3 `# Errors`", where, f"`{it.name}` returns a `Result` and its docs have no `# Errors`")
        if it.kind == "fn" and re.search(r"\bpanic!\(|\.unwrap\(\)|\.expect\(|\bunreachable!\(|\btodo!\(|(?<!debug_)assert(?:_eq|_ne)?!\(", it.body) \
                and "# Panics" not in text:
            audit.evidence("2.4", f"`{where}`: `{it.name}` can panic (its body has a panic, an `unwrap` "
                                  f"or an assertion) and its docs have no `# Panics`")
        for ex in exs:
            visible = [(n, t) for n, t in ex if not hidden(t)]
            setup = [(n, t) for n, t in ex if hidden(t) and not plumbing(t)]
            uses_q = any("?" in strip(t) for _, t in visible)
            if any(re.search(r"\.unwrap\(\)|\.expect\(", t) for _, t in visible):
                unwrapping += 1
                n = next(n for n, t in visible if re.search(r"\.unwrap\(\)|\.expect\(", t))
                returns_result = re.search(r"->\s*(?:[\w:]+::)?Result\b", it.signature)
                audit.find("short" if returns_result else "consider", "§2.1 examples use `?`",
                           f"{pkg.rel(it.path)}:{n}",
                           f"`{it.name}`'s example unwraps; the standard asks for `?`, with "
                           f"`# Ok::<(), Box<dyn std::error::Error>>(())` hidden"
                           + ("" if returns_result else " (it returns no `Result`: `?` on an "
                              "`Option` needs `.ok_or(...)`, so this may be the clearer choice)"))
            if uses_q and not any(re.search(r"Ok::<|fn main\(\)\s*->", t) for _, t in ex):
                audit.find("short", "§2.1 examples use `?`", f"{pkg.rel(it.path)}:{ex[0][0]}",
                           f"`{it.name}`'s example uses `?` without the hidden `# Ok::<...>(())` line")
            if setup:
                hidden_setup += len(setup)
                setups.append(f"`{pkg.rel(it.path)}:{setup[0][0]}`: `{it.name}`'s example hides "
                              f"{len(setup)} setup line(s), from `{setup[0][1].strip()[:70]}`")
        if it.kind == "enum" and it.name.endswith("Error") and not any("non_exhaustive" in a for a in it.attrs):
            if "exhaustive" in text:
                audit.evidence("2.6", f"`{where}`: `{it.name}` is exhaustive and its docs say why: "
                                      f"is the reason sound?")
            else:
                audit.find("short", "§2.6 error types", where,
                           f"`{it.name}` is a public error enum without `#[non_exhaustive]`, and its "
                           f"docs do not say why it is exhaustive (a code change, for a person to decide)",
                           judged="2.6")
        if re.search(r"\busize\b", it.signature):
            audit.evidence("2.7", f"`{where}`: `{it.name}` has `usize` in its signature: does the value "
                                  f"index this machine's memory, or come from someone else?")
    for line in setups[:15]:
        audit.evidence("2.2", line)
    if len(setups) > 15:
        audit.evidence("2.2", f"and {len(setups) - 15} more examples with hidden setup")
    audit.decide("2.1", "**§2, examples:** every example uses `?`, with the error plumbing hidden.")
    audit.decide("2.6", "**§2, error types:** every public error enum is `#[non_exhaustive]` or says why not.")
    if not audit.judged.get("2.2", Judged({})).evidence:
        audit.decide("2.2", "**§2, hidden setup:** no example hides a setup line.")
    fns = [i for i in found if i.kind == "fn"]
    audit.checks.append(Check("public items read (pubkit)", f"{len(found)} items, {len(fns)} functions, "
                                                            f"{hidden_setup} hidden setup line(s)"))


ROOT_SECTIONS = [  # (judged rule, heading pattern, what it is)
    ("1.2", re.compile(r"overview", re.I), "Overview"),
    ("1.4", re.compile(r"example", re.I), "Examples"),
    ("1.5", re.compile(r"\bwhy\b", re.I), "Why this crate?"),
    ("1.6", re.compile(r"related", re.I), "Related crates"),
    ("1.7", re.compile(r"feature", re.I), "Crate features"),
    ("1.8", re.compile(r"minimum (supported )?rust|msrv", re.I), "Minimum Rust version"),
    ("1.9", re.compile(r"version", re.I), "Versioning"),
]


def check_crate_root(pkg: Package, audit: Audit):
    """§1: the crate root's sections, in order; its features, MSRV and
    versioning against Cargo.toml and release.json; its Related crates
    against Cargo.lock."""
    if not pkg.library:
        return
    lib = pkg.rel(pkg.lib)
    source = pkg.lib.read_text(encoding="utf-8")
    if re.search(r"#!\[doc\s*=\s*include_str!\([^)]*README", source):
        audit.find("short", "§1 crate documentation", lib,
                   "the crate root includes the README; the standard asks for crate docs written in `lib.rs`")
    doc = crate_root_docs(pkg.lib)
    if not doc:
        audit.find("blocks", "§1 crate documentation", lib, "the crate root has no documentation")
        return
    heads = headings(doc)
    first_head = heads[0][0] if heads else None
    intro = [(n, t) for n, t in doc if first_head is None or n < first_head]
    if not examples(intro):
        audit.find("short", "§1.1 one sentence and an example", lib,
                   "the crate docs have no runnable example before their first heading", judged="1.1")
    found = []
    for key, pattern, name in ROOT_SECTIONS:
        if key == "1.9":
            line = next((n for n, _, t in heads if pattern.search(t) and not ROOT_SECTIONS[5][1].search(t)), None)
        else:
            line = next((n for n, _, t in heads if pattern.search(t)), None)
        if line is None:
            audit.find("short", f"§{key} {name}", lib, f"the crate docs have no {name} section", judged=key)
        else:
            found.append((line, key, name))
            audit.evidence(key, f"`{lib}:{line}`: the {name} section")
    order = [key for _, key, _ in sorted(found)]
    if order != sorted(order, key=lambda k: float(k)):
        audit.find("short", "§1 crate documentation", lib,
                   f"the sections come in the order {', '.join(n for _, _, n in sorted(found))}; "
                   f"the standard's order is Overview, Examples, Why, Related crates, Features, "
                   f"Minimum Rust version, Versioning")
    examples_at = next((n for n, key, _ in found if key == "1.4"), None)
    links = [(n, t) for n, t in doc if re.search(r"\]\(#", t) and (examples_at is None or n < examples_at)]
    if links:
        audit.evidence("1.3", f"`{lib}:{links[0][0]}`: {len(links)} line(s) link to sections of the page "
                              f"before Examples: does the map cover the page?")
    else:
        audit.find("short", "§1.3 a map of the page", lib,
                   "no line before Examples links to the page's sections", judged="1.3")
    check_features(pkg, audit, doc, lib)
    check_msrv(pkg, audit, doc, lib)
    check_versioning(pkg, audit, doc, lib)
    check_related(pkg, audit, section_of(doc, ROOT_SECTIONS[3][1]), lib, "1.6")


def check_features(pkg: Package, audit: Audit, doc, lib: str):
    text = " ".join(t for _, t in section_of(doc, ROOT_SECTIONS[4][1]))
    if not text:
        return
    features = sorted(f for f in pkg.pkg.get("features", {}) if f != "default" or pkg.pkg["features"][f])
    if not features and not re.search(r"\bnone\b|no (cargo )?features", text, re.I):
        audit.find("short", "§1.7 Crate features", lib, "the crate has no features, and its Crate features "
                   "section does not say \"None\"", judged="1.7")
    for f in features:
        if not re.search(rf"`{re.escape(f)}`|\b{re.escape(f)}\b", text):
            audit.find("short", "§1.7 Crate features", lib, f"the feature `{f}` is not in the Crate features section")
    audit.decide("1.7", "**§1, features:** the Crate features section names every feature Cargo.toml declares.")


def check_msrv(pkg: Package, audit: Audit, doc, lib: str):
    text = " ".join(t for _, t in section_of(doc, ROOT_SECTIONS[5][1]))
    msrv = pkg.pkg.get("rust_version") or ""
    if not text:
        return
    if msrv and msrv not in text and msrv.rsplit(".0", 1)[0] not in text:
        audit.find("short", "§1.8 Minimum Rust version", lib,
                   f"`rust-version` is {msrv}, which the Minimum Rust version section does not state")
    if not re.search(r"minor|second number|\by\b|0\.y", text, re.I):
        audit.find("short", "§1.8 Minimum Rust version", lib,
                   "the Minimum Rust version section states no policy for raising it")
    audit.decide("1.8", f"**§1, MSRV:** the section states {msrv or 'the rust-version'} and the policy.")


def check_versioning(pkg: Package, audit: Audit, doc, lib: str):
    lines = section_of(doc, re.compile(r"^versioning", re.I))
    text = " ".join(t for _, t in lines)
    if not text:
        return
    claims_checked = re.search(r"semver-checks|checked against|api is checked", text, re.I)
    if claims_checked and not pkg.release.get("semver-checks"):
        audit.find("blocks", "§1.9 Versioning", f"{lib}:{lines[0][0]}",
                   "the docs say the public API is checked against the last release, but release.json "
                   "does not set `\"semver-checks\": true` for this package: a false claim")
    elif not claims_checked:
        audit.find("short", "§1.9 Versioning", f"{lib}:{lines[0][0]}",
                   "the Versioning section does not say the public API is checked against the last release")
    if not re.search(r"cargo|semver|semantic", text, re.I):
        audit.find("short", "§1.9 Versioning", f"{lib}:{lines[0][0]}", "the Versioning section does not name Cargo's convention")
    audit.decide("1.9", "**§1, versioning:** Cargo's convention, and the API checked by cargo-semver-checks, "
                        "as release.json sets.")


def check_related(pkg: Package, audit: Audit, lines, where: str, key: str):
    """Each row of a Related crates (or tools) table: its version, against
    Cargo.lock when the crate is a dependency."""
    lock = pkg.lock_versions()
    table = [(n, t.strip()) for n, t in lines if t.strip().startswith("|")]
    separator = re.compile(r"\|\s*:?-")
    # A row over a |---| line is a header; a section may hold several tables.
    headers = {table[i][0] for i in range(len(table) - 1) if separator.match(table[i + 1][1])}
    rows = [(n, t) for n, t in table if not separator.match(t) and n not in headers]
    for n, t in rows:
        cells = [c.strip() for c in t.strip("|").split("|")]
        name = re.sub(r"[`*\[\]]|\(.*?\)", "", cells[0]).strip().split()[0] if cells and cells[0] else ""
        found = next((m for c in cells[1:] for m in [re.fullmatch(r"v?(\d+(?:\.\d+){0,2})", c)] if m), None) \
            or VERSION.search(" ".join(cells[1:]))
        version = found.group(1) if found else ""
        if not name:
            continue
        locked = lock.get(name) or lock.get(name.replace("-", "_")) or set()
        # A version short of three parts ("1", "0.22") is a requirement: it
        # matches each locked version it covers.
        covered = any(v == version or v.startswith(version + ".") for v in locked)
        if not version:
            audit.find("short", f"§{key} Related crates", f"{where}:{n}",
                       f"the row for `{name}` gives no version checked")
        elif locked and not covered:
            audit.find("consider", f"§{key} Related crates", f"{where}:{n}",
                       f"the row for `{name}` says {version}; Cargo.lock has "
                       f"{', '.join(sorted(locked))}: re-check the claim at that version")
        audit.evidence(key, f"`{where}:{n}`: `{name}` {version or '(no version)'}"
                            f"{'; Cargo.lock: ' + ', '.join(sorted(locked)) if locked else '; not a dependency'}"
                            f": is the claim checked by a run?")


def check_manifest(pkg: Package, audit: Audit):
    """§5: Cargo.toml's metadata and `include`, and how publish-check runs."""
    where = pkg.rel(pkg.manifest)
    for key in ("description", "license", "repository", "documentation", "keywords", "categories"):
        value = pkg.pkg.get(key.replace("-", "_"))
        if not value:
            audit.find("short", "§5.1 Cargo.toml", where, f"`{key}` is not set")
    if not pkg.pkg.get("rust_version"):
        audit.find("short", "§5.1 Cargo.toml", where, "`rust-version` is not set")
    include = pkg.field("include")
    if not include:
        audit.find("short", "§5.1 Cargo.toml", where, "there is no explicit `include` list")
    docsrs = pkg.toml.get("package", {}).get("metadata", {}).get("docs", {}).get("rs", {})
    if pkg.library:
        if docsrs.get("all-features") is not True:
            audit.find("short", "§5.1 Cargo.toml", where, "`[package.metadata.docs.rs]` does not set `all-features = true`")
        if "--generate-link-to-definition" not in (docsrs.get("rustdoc-args") or []):
            audit.find("short", "§5.1 Cargo.toml", where,
                       "`[package.metadata.docs.rs]` rustdoc-args lack `--generate-link-to-definition`")
    else:
        doc = pkg.pkg.get("documentation") or ""
        if doc and "readme" not in doc.lower() and "docs.rs" in doc:
            audit.find("short", "§9.6 metadata", where, f"`documentation` is {doc}; for a command-line "
                       f"tool it points at the README", judged="9.6")
        audit.decide("9.6", "**§9, metadata:** `documentation` points at the README.")
    audit.decide("5.1", "**§5, metadata:** description, licence, repository, documentation, keywords, "
                        "categories, `include` and `rust-version` are set"
                        + (", with docs.rs metadata." if pkg.library else "."))
    for pattern in include or []:
        if not pattern.startswith("/"):
            audit.find("short", "§5.2 `include` anchored", where,
                       f"the include pattern \"{pattern}\" is not anchored with `/`")
    audit.decide("5.2", "**§5, include:** every pattern is anchored with `/`.")
    mk = pkg.root / "pubkit.mk"
    recipe = mk.read_text(encoding="utf-8").split("\npublish-check:", 1) if mk.is_file() else ["", ""]
    if len(recipe) < 2 or "CARGO_TARGET_DIR=$(CURDIR)/target/publish-check" not in recipe[1].split("\n\n", 1)[0]:
        audit.find("short", "§5.3 publish-check", "pubkit.mk" if mk.is_file() else "",
                   "`make publish-check` does not build in a target directory of its own (`pubkit sync` writes it)")
    audit.decide("5.3", "**§5, publish-check:** pubkit.mk's `publish-check` builds in `target/publish-check`.")


README_LIBRARY = [("a Documentation link", re.compile(r"docs\.rs|documentation", re.I)),
                  ("Usage", re.compile(r"usage|install", re.I)),
                  ("Minimum Rust version", re.compile(r"minimum (supported )?rust|msrv", re.I)),
                  ("Security", re.compile(r"security", re.I))]
README_TOOL = [("an overview of its commands", re.compile(r"command|usage", re.I)),
               ("why this tool", re.compile(r"\bwhy\b", re.I)),
               ("related tools", re.compile(r"related", re.I)),
               ("installation", re.compile(r"install", re.I)),
               ("Minimum Rust version", re.compile(r"minimum (supported )?rust|msrv", re.I)),
               ("versioning", re.compile(r"version", re.I))]


def check_readme(pkg: Package, audit: Audit):
    path = pkg.readme()
    if path is None:
        audit.find("short", "§5.5 README", "", "the package has no README")
        return
    where = pkg.rel(path)
    doc = markdown(path)
    heads = " | ".join(t for _, _, t in headings(doc))
    text = "\n".join(t for _, t in doc)
    key, wanted = ("5.5", README_LIBRARY) if pkg.library else ("9.1", README_TOOL)
    for name, pattern in wanted:
        if not pattern.search(heads) and not (name == "a Documentation link" and pattern.search(text)):
            audit.find("short", f"§{key} README", where, f"the README has no {name} section")
    if "```" not in text:
        audit.find("short", f"§{key} README", where, "the README has no example")
    audit.evidence(key, f"`{where}`: sections {heads[:300]}")
    if not pkg.library:
        check_related(pkg, audit, section_of(doc, re.compile(r"related", re.I)), where, "9.8")


TOOLS = [  # (name, what SECURITY.md says, the pubkit jobs that run it, a command that does)
    ("cargo deny", re.compile(r"cargo[ -]deny", re.I), {"job-rust-audit"}, re.compile(r"cargo deny|make deny")),
    ("cargo vet", re.compile(r"cargo[ -]vet", re.I), {"job-rust-audit"}, re.compile(r"cargo vet|make vet")),
    ("OSV-Scanner", re.compile(r"\bOSV\b|osv-scanner", re.I), {"job-osv"}, re.compile(r"osv-scanner")),
    ("fuzzing", re.compile(r"\bfuzz", re.I), {"job-rust-fuzz"}, re.compile(r"cargo fuzz|fuzz-smoke")),
    ("the 32-bit and big-endian tests", re.compile(r"\bi686\b|\bs390x\b|big-endian|32-bit", re.I),
     {"job-rust-cross"}, re.compile(r"\bcross\b")),
    ("miri", re.compile(r"\bmiri\b", re.I), set(), re.compile(r"\bmiri\b")),
    ("dependency review", re.compile(r"dependency review|dependency-review", re.I), {"job-review"},
     re.compile(r"dependency-review-action")),
]
RUST_CI = {"job-rust-gates", "job-rust-test", "job-rust-functional", "job-rust-msrv", "job-rust-fuzz",
           "job-rust-cross", "job-rust-audit", "job-osv", "job-review", "job-sonar"}


def workflows(root: Path) -> list[dict]:
    """Each workflow: its file, its triggers, the pubkit jobs it calls, its text."""
    out = []
    for path in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        jobs = set(re.findall(r"schryer/pubkit/\.github/workflows/(job-[a-z-]+)\.yml@", text))
        if re.search(r"schryer/pubkit/\.github/workflows/rust-ci\.yml@", text):
            jobs |= RUST_CI
        on = text.split("\njobs:", 1)[0]
        triggers = {t for t in ("pull_request", "push", "schedule", "workflow_dispatch") if re.search(rf"\b{t}\b", on)}
        out.append({"file": f".github/workflows/{path.name}", "triggers": triggers, "jobs": jobs, "text": text})
    return out


def runs(flows: list[dict], tool: tuple) -> list[dict]:
    _, _, jobs, command = tool
    return [w for w in flows if w["jobs"] & jobs or command.search(w["text"])]


def check_security(pkg: Package, audit: Audit):
    """§7: SECURITY.md's claims against the workflows that would make them true."""
    path = next((p for p in (pkg.dir / "SECURITY.md", pkg.root / "SECURITY.md") if p.is_file()), None)
    if path is None:
        audit.find("blocks", "RS-SUP-04", "", "there is no SECURITY.md")
        return
    where = pkg.rel(path)
    lines = markdown(path)
    text = "\n".join(t for _, t in lines)
    flows = workflows(pkg.root)
    if not re.search(r"security/advisories|privately|private (vulnerability )?report", text, re.I):
        audit.find("short", "§7.1 SECURITY.md", where, "it does not say how to report a vulnerability privately")
    mentioned = set()
    for n, line in lines:
        for tool in TOOLS:
            name, says = tool[0], tool[1]
            if not says.search(line):
                continue
            mentioned.add(name)
            running = runs(flows, tool)
            if not running:
                audit.find("short", "§7.1 SECURITY.md", f"{where}:{n}",
                           f"it names {name}, which no workflow in .github/workflows runs: if the "
                           f"claim is that it runs, it is false, which blocks a release")
                continue
            if re.search(r"pull request", line, re.I) and not any("pull_request" in w["triggers"] for w in running):
                audit.find("short", "§7.1 SECURITY.md", f"{where}:{n}",
                           f"it says {name} runs on pull requests; only "
                           f"{', '.join(w['file'] for w in running)} run it, none on `pull_request`")
            if re.search(r"nightly|daily|every day|schedule", line, re.I) and not any("schedule" in w["triggers"] for w in running):
                audit.find("short", "§7.1 SECURITY.md", f"{where}:{n}",
                           f"it says {name} runs on a schedule; no scheduled workflow runs it")
            audit.evidence("7.1", f"`{where}:{n}`: {name}, run by "
                                  f"{', '.join(w['file'] + ' (' + ', '.join(sorted(w['triggers'])) + ')' for w in running)}")
    for tool in TOOLS[:4]:
        if tool[0] not in mentioned and runs(flows, tool):
            audit.find("consider", "§7.1 SECURITY.md", where,
                       f"{', '.join(w['file'] for w in runs(flows, tool))} run {tool[0]}, which SECURITY.md does not mention")
    config = pkg.root / "supply-chain" / "config.toml"
    if config.is_file():
        imports = sorted(tomllib.loads(config.read_text(encoding="utf-8")).get("imports", {}))
        spelled = lambda i: re.escape(i).replace(r"\-", "[- ]")
        named = [i for i in imports if re.search(rf"\b{spelled(i)}\b", text, re.I)]
        if named and len(named) < len(imports):
            missing = [i for i in imports if i not in named]
            audit.find("short", "§7.1 SECURITY.md", where,
                       f"it names the imported audit sets {', '.join(named)} but not "
                       f"{', '.join(missing)}, which supply-chain/config.toml imports")


def check_tests(pkg: Package, audit: Audit):
    """§3, §4 and §9: the evidence a person needs for the judged test rules."""
    testdata = [d for d in (pkg.dir / "testdata", pkg.root / "testdata") if d.is_dir()]
    sources = set()
    for d in testdata:
        for f in sorted(d.rglob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            for case in data if isinstance(data, list) else data.get("cases", data.get("vectors", [])) if isinstance(data, dict) else []:
                if isinstance(case, dict) and case.get("source"):
                    sources.add(str(case["source"])[:120])
            if isinstance(data, dict) and data.get("source"):
                sources.add(str(data["source"])[:120])
    for s in sorted(sources)[:12]:
        audit.evidence("3.1", f"a source: {s}")
    if testdata and not sources:
        audit.evidence("3.1", "no case in testdata/ names a `source`")
    dev = set(pkg.toml.get("dev-dependencies", {}))
    if "proptest" in dev:
        audit.evidence("3.2", "proptest is a dev-dependency")
    else:
        audit.find("consider", "§3.2 property tests", pkg.rel(pkg.manifest), "proptest is not a dev-dependency")
    if dev:
        audit.evidence("3.3", f"dev-dependencies: {', '.join(sorted(dev))}")
    count = 0
    for f in pkg.sources() + sorted((pkg.dir / "tests").rglob("*.rs")):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), start=1):
            if COVERS.search(line):
                count += 1
                if count <= 5:
                    audit.evidence("3.4", f"`{pkg.rel(f)}:{n}`: {line.strip()}")
    audit.evidence("3.4", f"{count} `// covers:` comments in all; spot-check that each test exercises what it claims")
    targets = sorted(p.stem for p in (pkg.root / "fuzz" / "fuzz_targets").glob("*.rs"))
    audit.evidence("4.1", f"fuzz targets: {', '.join(targets) or 'none'}")
    entry = [i for i in items(pkg.sources()) if i.kind == "fn"
             and re.search(r"(^|::)(parse|from_bytes|from_slice|from_str|decode|verify|read|load|deserialize)\w*$", i.name)]
    for i in entry[:15]:
        audit.evidence("4.1", f"`{pkg.rel(i.path)}:{i.line}`: `{i.name}` reads outside input: has it a target?")
    if not pkg.library:
        features = pkg.root / "tests" / "features"
        if features.is_dir() and any(features.glob("*.feature")):
            audit.evidence("9.4", f"tests/features has {len(list(features.glob('*.feature')))} feature files")
        else:
            audit.find("short", "§9.4 tested end to end", "", "there is no functional suite (tests/features)", judged="9.4")


def check_writing(pkg: Package, audit: Audit):
    """§10: words the standard rules out, and sentences joined by a semicolon."""
    files = [p for p in [pkg.readme(), pkg.root / "SECURITY.md"] if p and p.is_file()]
    hits = []
    for f in files + pkg.sources():
        code = False
        lines = list(enumerate(f.read_text(encoding="utf-8").splitlines(), start=1))
        if f.suffix == ".rs":
            root = dict(crate_root_docs(f)) if f == pkg.lib else {}
            lines = [(n, root[n]) if n in root else (n, t.strip()[3:]) for n, t in lines
                     if n in root or t.strip().startswith(("///", "//!"))]
        for n, line in lines:
            prose = line.strip()
            if prose.startswith("```"):
                code = not code
                continue
            if code or not prose or prose.startswith(("|", "#", "-  ")) and "`" in prose[:3]:
                continue
            words = re.findall(r"\b(simply|just|easily|obviously|blazing(?:ly)?)\b", prose, re.I)
            if words:
                hits.append(f"`{pkg.rel(f)}:{n}`: \"{words[0]}\"")
            if re.search(r"[a-z]; [a-z]", re.sub(r"`[^`]*`", "x", prose)):
                hits.append(f"`{pkg.rel(f)}:{n}`: two ideas joined by a semicolon")
    for h in hits[:20]:
        audit.evidence("10.1", h)
    if len(hits) > 20:
        audit.evidence("10.1", f"and {len(hits) - 20} more")


# --- checks that take minutes ------------------------------------------------------


def check_docsrs(pkg: Package, audit: Audit):
    """§5.4: the docs build from the packaged crate on nightly, as docs.rs builds them."""
    if not pkg.library:
        return
    if run(["cargo", "+nightly", "--version"], pkg.root).returncode:
        audit.not_run.append(Check("docs.rs build", "no nightly toolchain"))
        return
    target = pkg.root / "target" / "audit"
    packaged = run(["cargo", "package", "--no-verify", "--allow-dirty", "-p", pkg.name],
                   pkg.root, env={"CARGO_TARGET_DIR": str(target)})
    crate = target / "package" / f"{pkg.name}-{pkg.pkg['version']}.crate"
    if packaged.returncode or not crate.is_file():
        audit.find("blocks", "§5.4 docs.rs build", "", f"`cargo package -p {pkg.name}` fails: {tail(packaged.stderr)}")
        audit.checks.append(Check("docs.rs build", "packaging fails"))
        return
    flags = "--cfg docsrs -Z unstable-options --generate-link-to-definition -D warnings"
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(crate) as tar:
            tar.extractall(tmp, filter="data")
        unpacked = Path(tmp) / f"{pkg.name}-{pkg.pkg['version']}"
        proc = run(["cargo", "+nightly", "doc", "--no-deps", "--all-features"], unpacked,
                   env={"RUSTDOCFLAGS": flags, "CARGO_TARGET_DIR": str(target / "docsrs")})
    if proc.returncode:
        audit.find("blocks", "§5.4 docs.rs build", "", f"the docs.rs build fails: {tail(proc.stderr)}",
                   judged="5.4")
        audit.checks.append(Check(f"docs.rs build: packaged crate, `cargo +nightly doc --no-deps "
                                  f"--all-features` with `{flags}`", "fails"))
    else:
        audit.checks.append(Check(f"docs.rs build: packaged crate, `cargo +nightly doc --no-deps "
                                  f"--all-features` with `{flags}`", "pass, no warnings"))
    audit.decide("5.4", "**§5, docs.rs:** the packaged crate's docs build on nightly with docs.rs's flags, "
                        "with no warnings.")


def check_slow(pkg: Package, audit: Audit, waived: set[str]):
    """publish --dry-run and the MSRV build for this package, and the
    standard's other make targets, which act on the whole repository."""
    env = {"CARGO_TARGET_DIR": str(pkg.root / "target" / "publish-check")}
    args = ["cargo", "publish", "--dry-run", "--locked", "-p", pkg.name] + (["--allow-dirty"] if pkg.dirty else [])
    proc = run(args, pkg.root, env=env)
    note = " (with --allow-dirty: the tree has uncommitted changes)" if pkg.dirty else ""
    if proc.returncode:
        audit.find("blocks", "RS-PKG-01, `make publish-check`", "", f"`cargo publish --dry-run` fails: {tail(proc.stderr)}")
    audit.checks.append(Check(f"`cargo publish --dry-run --locked -p {pkg.name}`{note}",
                              "pass" if proc.returncode == 0 else "fails"))
    msrv = pkg.pkg.get("rust_version")
    if "RS-VER-02" not in waived and msrv:
        if run(["cargo", f"+{msrv}", "--version"], pkg.root).returncode:
            audit.not_run.append(Check(f"MSRV build (Rust {msrv})", f"no {msrv} toolchain: rustup toolchain install {msrv}"))
        else:
            proc = run(["cargo", f"+{msrv}", "check", "--locked", "-p", pkg.name], pkg.root)
            if proc.returncode:
                audit.find("blocks", "RS-VER-02, `make msrv`", "", f"the crate does not build on Rust {msrv}: {tail(proc.stderr)}")
            audit.checks.append(Check(f"`cargo +{msrv} check --locked -p {pkg.name}` (MSRV)",
                                      "pass" if proc.returncode == 0 else "fails"))
    rules = standard.load()
    plan = standard.required(rules, pkg.library, frozenset(waived))
    wanted = [t for t in plan["targets"]["gates"] + plan["targets"]["functional"] + plan["targets"]["audit"]
              if t in ("doc-coverage-check", "supply-chain-check", "vectors-check", "deny", "vet")]
    if not (pkg.root / "Makefile").is_file():
        audit.not_run.append(Check("make targets", "no Makefile"))
        return
    for target in wanted:
        proc = run(["make", "-s", target], pkg.root)
        ids = [r["id"] for r in rules["rules"] if any(c.get("target") == target for c in r["checks"])]
        if proc.returncode:
            audit.find("blocks", f"{', '.join(ids)}, `make {target}`", "", f"`make {target}` fails: {tail(proc.stdout + proc.stderr)}")
        audit.checks.append(Check(f"`make {target}` (whole repository)",
                                  "pass" if proc.returncode == 0 else "fails"))


# --- the report ------------------------------------------------------------------


def rule_text(rule: dict) -> str:
    """A judged rule's words, without its list marker or its tags."""
    text = " ".join(re.sub(r"\[(?:judged|checked)[^\]]*\]", "", rule["text"]).split())
    return re.sub(r"^(?:- |\d+\. )", "", text).strip()


def rule_label(j: Judged) -> str:
    return f"§{j.rule['id']} {j.rule['title']}"


def render(pkg: Package, audit: Audit, today: str, quick: bool, waived: dict[str, str]) -> str:
    blocking = sum(1 for f in audit.findings if f.severity == "blocks")
    review = [j for j in audit.judged.values() if j.status == "review"]
    kind = "a library" if pkg.library else "a command-line tool (binary only): section 9 applies"
    out = [
        "---",
        f"package: {pkg.name}",
        f"version: {pkg.version}",
        f"standard: {current()}",
        f"blocking: {blocking}",
        f"date: {today}",
        f"auditor: pubkit audit prepare (pubkit {__version__})",
        f"commit: {pkg.commit}",
        f"last-release: {pkg.last_tag or 'none'}",
        "---",
        "",
        f"# Quality audit: {pkg.name} {pkg.version}",
        "",
        f"Prepared by `pubkit audit prepare` at `{pkg.commit[:7] or 'no commit'}`"
        f"{' plus uncommitted changes' if pkg.dirty else ''}, against the Rust crate standard named "
        f"above. {pkg.name} is {kind}. {pkg.version} is the version to release"
        f" (`pubrel next --package {pkg.name}`), after "
        f"{('`' + pkg.last_tag + '`') if pkg.last_tag else 'no earlier release'}.",
        "",
        f"Every rule a machine can decide is decided below, each finding with its file and line. "
        f"{len(review)} rule(s) that need judgement are under \"Needs review\", each with its "
        f"mark and what the machine found for it. `pubkit audit --check` refuses this report "
        f"while any mark remains: a person judges each rule, moves what they find into the "
        f"sections above it with `blocking` counted again, and removes the mark. "
        f"`pubkit audit draft --package {pkg.name}` drafts those verdicts for that review. "
        f"This file should be committed with the release's changes.",
    ]
    n = 0
    for severity, title in SEVERITIES:
        out += ["", f"## {title}", ""]
        found = [f for f in audit.findings if f.severity == severity]
        if not found:
            out.append("None.")
        for f in found:
            n += 1
            where = f" (`{f.where}`)" if f.where else ""
            out.append(f"{n}. **{f.rule}**{where}. {f.text}.")
    out += ["", "## Needs review", ""]
    if not review:
        out.append("None.")
    for j in review:
        r = j.rule
        out += [f"<!-- rule {r['id']} review -->", f"### {rule_label(j)} {REVIEW}", ""]
        words = rule_text(r)
        out.append(f"The standard, section {r['section']}: {words[:700]}{'...' if len(words) > 700 else ''}")
        if r.get("question"):
            out += ["", f"To judge: {r['question']}"]
        out += [""]
        if j.evidence:
            out.append("What the machine found:")
            out += [f"- {e}" for e in j.evidence]
        else:
            out.append("The machine found nothing to check here: read the code and docs.")
        out += ["<!-- /rule -->", ""]
    if out[-1] == "":
        out.pop()
    out += ["", "## Sections met", ""]
    out += [f"- {m}" for m in audit.met] or ["None decided by machine alone."]
    applies_not = [j for j in audit.judged.values() if j.status == "n/a"]
    if applies_not:
        out.append(f"- **Not applicable to {kind.split(':')[0]}:** "
                   + ", ".join(f"§{j.rule['id']}" for j in applies_not) + ".")
    for rule, reason in sorted(waived.items()):
        out.append(f"- **{rule}:** under a declared exception ({reason}), so not reported as a gap.")
    out += ["", "## Checks run", "", "| Check | Result |", "|---|---|"]
    out += [f"| {c.name} | {c.result} |" for c in audit.checks]
    out += ["", "## Checks not run", ""]
    not_run = list(audit.not_run)
    if quick:
        not_run.append(Check("docs.rs build, `cargo publish --dry-run`, the MSRV build and the make targets",
                             "`--quick` skips them; run `pubkit audit prepare` without it before the release"))
    not_run.append(Check("`make fuzz-smoke` and `make functional`", "CI runs them on the release pull request"))
    out += [f"- **{c.name}:** {c.result}." for c in not_run]
    return "\n".join(out) + "\n"


def prepare(root: Path, name: str, version: str | None, out: str | None,
            quick: bool, force: bool) -> int:
    pkg = Package(root, name, version)
    rules = standard.load()
    from .conform import exceptions
    waived_ids, open_, _ = exceptions(pkg.config, {r["id"] for r in rules["rules"]})
    waived = {e.split(" until ")[0]: e.split(": ", 1)[-1] for e in open_}
    audit = Audit()
    for r in rules["judged"]:
        applies = (r["applies"] == "every crate" or (r["applies"] == "library") == pkg.library)
        audit.judged[r["id"]] = Judged(r, "review" if applies else "n/a")
    if "RS-TEST-01" in waived_ids:
        audit.judged["3.1"].status = "n/a"
    from .paths import confined
    target = confined(out, "the --out file") if out else root / DIR / f"{pkg.name}-{pkg.version}.md"
    if target.exists() and not force:
        sys.exit(f"pubkit: {pkg.rel(target)} exists, and may hold a person's review: give --force "
                 f"to write over it, or --out for another file")
    print(f"auditing {pkg.name} {pkg.version} at {pkg.commit[:12]}"
          f" (last release {pkg.last_tag or 'none'})", file=sys.stderr)
    check_crate_root(pkg, audit)
    check_items(pkg, audit)
    check_manifest(pkg, audit)
    check_readme(pkg, audit)
    check_security(pkg, audit)
    check_tests(pkg, audit)
    check_writing(pkg, audit)
    check_builds(pkg, audit)
    if not quick:
        check_docsrs(pkg, audit)
        check_slow(pkg, audit, waived_ids)
    today = os.environ.get("PUBKIT_TODAY") or datetime.date.today().isoformat()
    text = render(pkg, audit, today, quick, waived)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    review = sum(1 for j in audit.judged.values() if j.status == "review")
    blocking = sum(1 for f in audit.findings if f.severity == "blocks")
    print(f"wrote {pkg.rel(target)}: {len(audit.findings)} finding(s), {blocking} blocking; "
          f"{review} rule(s) need review")
    return 0
