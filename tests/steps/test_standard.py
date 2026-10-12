"""Steps for the standard's rule IDs, `pubkit required` and `pubkit
doc-coverage`, run against scratch Rust crates. cargo reads them
(`cargo metadata`) but builds nothing, so no network is needed."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pytest_bdd import given, parsers, scenarios, then, when

from pubkit import standard

SOURCE = Path(__file__).resolve().parents[2]

scenarios("standard.feature")
scenarios("doc-coverage.feature")


def crate(repo: Path, name: str, lib: str | None, main: bool = False, tests: str = "") -> None:
    (repo / "Cargo.toml").write_text(
        f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n'
        f'repository = "https://example.org/{name}"\n')
    (repo / "src").mkdir()
    if lib is not None:
        (repo / "src" / "lib.rs").write_text(lib)
    if main:
        (repo / "src" / "main.rs").write_text("fn main() {}\n")
    if tests:
        (repo / "tests").mkdir()
        (repo / "tests" / "it.rs").write_text(tests)
    (repo / "pubkit.json").write_text('{"kind": "rust"}\n')


TWICE = '''//! A crate.

/// Twice `n`.
///
/// ```
/// assert_eq!(demo::twice(2), 4);
/// ```
pub fn twice(n: u32) -> u32 {
    n * 2
}
'''
TEST = '''// covers: twice
#[test]
fn doubles() {
    assert_eq!(demo::twice(3), 6);
}
'''


@given(parsers.parse('a Rust repository whose crate "{name}" has a library'))
def rust_library(repo, name):
    crate(repo, name, TWICE)


@given(parsers.parse('a Rust repository whose crate "{name}" is only a binary'))
def rust_binary(repo, name):
    crate(repo, name, None, main=True)


@given(parsers.parse('a library "{name}" whose public function "{fn}" has an example and a test that covers it'))
def library_covered(repo, name, fn):
    crate(repo, name, TWICE, tests=TEST)


@given(parsers.parse('a library "{name}" whose public function "{fn}" has no example'))
def library_no_example(repo, name, fn):
    crate(repo, name, f"//! A crate.\n\n/// No example.\npub fn {fn}() {{}}\n")


@given(parsers.parse('a library "{name}" whose test covers "{item}", which does not exist'))
def library_bad_cover(repo, name, item):
    crate(repo, name, TWICE, tests=f"// covers: {item}\n#[test]\nfn t() {{}}\n")


@given("GITHUB_OUTPUT names a file")
def github_output(repo, env):
    env["GITHUB_OUTPUT"] = str(repo / "github-output")


@then(parsers.parse('the GITHUB_OUTPUT file says "{line}"'))
def github_output_says(repo, line):
    assert line in (repo / "github-output").read_text().splitlines()


@when("standard.json is generated from the standard", target_fixture="generated")
def generated():
    return standard.generate(SOURCE)


@then("it is the committed standard.json")
def committed(generated):
    assert generated == (SOURCE / standard.GENERATED).read_text(), "run `make standard`"


@when("I read the standard", target_fixture="text")
def read_standard():
    return (SOURCE / standard.SOURCE).read_text()


@then('every "[checked" tag names a rule ID')
def every_tag_has_id(text):
    # The legend, **[checked]**, names the mark rather than a rule.
    tags = re.findall(r"(?<![`*])\[checked[^\]]*\]", text, re.S)
    assert tags
    bare = [t for t in tags if not re.match(r"\[checked:\s*RS-[A-Z]+-\d{2},", t)]
    assert not bare, bare


@then("no rule ID tags two rules")
def ids_unique(text):
    standard.parse(text)  # raises on a repeated ID


@when("I read the standard.json pubkit ships", target_fixture="shipped")
def shipped():
    return json.loads((SOURCE / standard.GENERATED).read_text())


@then("the rust pubkit.mk defines every target it names")
def makefile_has_targets(shipped):
    mk = (SOURCE / "src" / "pubkit" / "templates" / "rust" / "pubkit.mk").read_text()
    defined = set(re.findall(r"^([a-z-]+):", mk, re.M))
    named = {b["target"] for b in shipped["baseline"]}
    named |= {c["target"] for r in shipped["rules"] for c in r["checks"] if c["kind"] == "make"}
    assert named <= defined, f"pubkit.mk lacks {sorted(named - defined)}"
