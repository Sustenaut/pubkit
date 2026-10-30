"""`pubkit doc-coverage`: every public function of a published library has
an example, and every public item says which tests cover it.

For each crate this:

- lists its public functions and methods (`pub fn`; with `unreachable_pub`
  every `pub` item is reachable) and fails if any has no example in its
  documentation;
- reads `// covers: Item` comments in the crate's tests and sources -- a test
  names the items it exercises, such as `// covers: Object::parse, Cid::of`
  -- and fails if one names an item that does not exist;
- writes the crate's TESTING.md beside its Cargo.toml: for every public
  item, how many examples its documentation runs and which tests cover it.

TESTING.md is a view, never edited by hand. `make doc-coverage` writes it;
`make doc-coverage-check` (in CI) fails if it is stale or the rules above
are broken. The crates are the published packages with a library target,
from `cargo metadata`, unless named.

It lives here so every repository runs the same version.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

FN = re.compile(r"\s*pub (?:const )?fn (\w+)")
TYPE = re.compile(r"\s*pub (?:struct|enum|trait|type) (\w+)")
IMPL = re.compile(r"\s*impl(?:<[^>]*>)?\s+")
TRAIT = re.compile(r"[\w:<>, ]+")
TEST_FN = re.compile(r"\s*(?:pub )?fn (\w+)")
COVERS = re.compile(r"//\s*covers:\s*(.+)")


def impl_of(line: str) -> str | None:
    """The type an `impl` line implements for: `Foo` in `impl Foo` and in
    `impl<T> Trait<T> for Foo<T>`. Split on ` for ` rather than matched in
    one pattern, which would backtrack."""
    m = IMPL.match(line)
    if not m:
        return None
    rest = line[m.end():]
    trait, sep, target = rest.partition(" for ")
    if sep and TRAIT.fullmatch(trait.rstrip()):
        rest = target.lstrip()
    name = re.match(r"\w+", rest)
    return name.group(0) if name else None


def strip(line: str) -> str:
    """The line without string literals or a trailing comment, for brace counting."""
    line = re.sub(r'"(?:\\.|[^"\\])*"', '""', line)
    line = re.sub(r"'(?:\\.|[^'\\])'", "''", line)
    return line.split("//", 1)[0]


def public_module(src: Path, lib: Path, path: Path) -> str:
    """The module path users write for items defined in `path`.

    Only modules declared `pub mod` appear: a private module's items are
    re-exported into its parent (`mod sig; pub use sig::sign;` makes `sign`
    a crate-root function), so they are named from there.
    """
    rel = path.relative_to(src).with_suffix("")
    parts = [p for p in rel.parts if p not in (lib.stem, "mod")]
    chain, parent = [], lib
    for part in parts:
        declared = parent.read_text() if parent.exists() else ""
        if re.search(rf"^\s*pub mod {part}\s*;", declared, re.M):
            chain.append(part)
        directory = parent.parent if parent.name in (lib.name, "mod.rs") else parent.with_suffix("")
        parent = directory / part / "mod.rs"
        if not parent.exists():
            parent = directory / f"{part}.rs"
    return "::".join(chain)


class Crate:
    """A published library: its name, directory, library root and binaries."""

    def __init__(self, name: str, directory: Path, lib: Path, skip: set[Path], repo: str):
        self.name, self.dir, self.lib, self.skip, self.repo = name, directory, lib, skip, repo


def crates(root: Path, names: list[str]) -> list[Crate]:
    meta = json.loads(subprocess.run(
        ["cargo", "metadata", "--no-deps", "--format-version", "1"],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout)
    out = []
    packages = {pkg["name"] for pkg in meta["packages"]}
    unknown = set(names) - packages
    if unknown:
        sys.exit(f"pubkit: no crate here named {', '.join(sorted(unknown))}")
    for pkg in meta["packages"]:
        lib = next((t for t in pkg["targets"] if "lib" in t["kind"]), None)
        # A named crate with no library, a command-line tool, has no
        # TESTING.md: its README and --help are its documentation.
        if lib is None or (names and pkg["name"] not in names):
            continue
        if not names and pkg.get("publish") == []:
            continue
        skip = {Path(t["src_path"]) for t in pkg["targets"] if "bin" in t["kind"]}
        repo = (pkg.get("repository") or "").rstrip("/")
        out.append(Crate(pkg["name"], Path(pkg["manifest_path"]).parent, Path(lib["src_path"]),
                         skip, repo))
    return out


def items(crate: Crate) -> list[tuple]:
    """Public items: (name, kind, file, line, examples)."""
    out = []
    src = crate.lib.parent
    for path in sorted(p for p in src.rglob("*.rs") if p not in crate.skip):
        lines = path.read_text().splitlines()
        depth, impl_stack, in_test = 0, [], None
        module = public_module(src, crate.lib, path)
        for i, line in enumerate(lines):
            if in_test is None and re.match(r"\s*#\[cfg\(test\)\]", line):
                in_test = depth
            code = strip(line)
            if in_test is None:
                impl = impl_of(line) if line.strip().startswith("impl") else None
                fn, ty = FN.match(line), TYPE.match(line)
                if fn or ty:
                    j, doc = i - 1, []
                    while j >= 0 and (lines[j].strip().startswith("///")
                                      or lines[j].strip().startswith("#[")):
                        doc.append(lines[j])
                        j -= 1
                    examples = sum(1 for d in doc if d.strip().startswith("/// ```")) // 2
                    if fn:
                        owner = impl_stack[-1][1] if impl_stack else module
                        name = f"{owner}::{fn.group(1)}" if owner else fn.group(1)
                        out.append((name, "fn", path, i + 1, examples))
                    else:
                        out.append((ty.group(1), "type", path, i + 1, examples))
                if impl and "{" in code:
                    impl_stack.append((depth, impl))
            depth += code.count("{") - code.count("}")
            while impl_stack and depth <= impl_stack[-1][0]:
                impl_stack.pop()
            if in_test is not None and depth <= in_test and "}" in code:
                in_test = None
    return out


def covered(crate: Crate, root: Path) -> dict:
    """Tests that say what they cover: {item: [(file, test, line)]}."""
    found: dict = {}
    files = list((crate.dir / "tests").rglob("*.rs")) + list(crate.lib.parent.rglob("*.rs"))
    for path in sorted(files):
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            m = COVERS.search(line)
            if not m:
                continue
            test = None
            for later in lines[i + 1:i + 8]:
                t = TEST_FN.match(later)
                if t:
                    test = t.group(1)
                    break
            for name in (n.strip() for n in m.group(1).split(",")):
                if name:
                    found.setdefault(name, []).append((path.relative_to(root), test, i + 1))
    return found


def report(crate: Crate, public: list[tuple], tests: dict) -> str:
    fns = [p for p in public if p[1] == "fn"]
    with_examples = sum(1 for p in fns if p[4] > 0)
    with_tests = sum(1 for p in public if p[0] in tests)
    lines = [
        f"# Testing: {crate.name}",
        "",
        "Generated by `pubkit doc-coverage`; do not edit.",
        "",
        f"{len(public)} public items; {with_examples} of {len(fns)} functions have examples, "
        f"and {with_tests} items name the tests that cover them.",
        "",
        "Every example in the documentation runs as a test (`cargo test --doc`). A",
        "test is listed against an item when its `// covers:` comment says it exercises",
        "that item: as the subject of the test, or within the same module to set it up.",
        "A test claims only items of the module it tests. The files are linked below.",
        "",
        "| Item | Examples | Tests |",
        "|---|---|---|",
    ]
    for name, kind, _path, _line, examples in sorted(public, key=lambda p: p[0].lower()):
        names = sorted({f"`{t}`" for _, t, _ in tests.get(name, []) if t})
        lines.append(f"| `{name}` | {examples if kind == 'fn' or examples else '-'} | "
                     f"{', '.join(names) if names else '-'} |")
    files = sorted({str(f) for v in tests.values() for f, _, _ in v})
    if files:
        lines += ["", "Test files:", ""]
        if crate.repo:
            lines += [f"- [`{f}`]({crate.repo}/blob/main/{f})" for f in files]
        else:
            lines += [f"- `{f}`" for f in files]
    return "\n".join(lines) + "\n"


def run(root: Path, names: list[str], check: bool) -> int:
    problems = []
    for crate in crates(root, names):
        public = items(crate)
        known = {p[0] for p in public}
        tests = covered(crate, root)
        for p in public:
            if p[1] == "fn" and p[4] == 0:
                problems.append(f"{p[2].relative_to(root)}:{p[3]}: `{p[0]}` has no example")
        for name, sites in tests.items():
            if name not in known:
                for f, _, line in sites:
                    problems.append(f"{f}:{line}: covers `{name}`, which is not a public item "
                                    f"of {crate.name}")
        text = report(crate, public, tests)
        path = crate.dir / "TESTING.md"
        if check:
            if not path.exists() or path.read_text() != text:
                problems.append(f"{path.relative_to(root)} is stale; run `make doc-coverage`")
            else:
                print(f"{path.relative_to(root)} current for {crate.name}")
        else:
            path.write_text(text)
            print(f"wrote {path.relative_to(root)}")
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1 if check else 0
    return 0
