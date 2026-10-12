"""Steps for `pubkit conform`, against a scratch crate that meets the
standard and is then changed one way at a time."""

from __future__ import annotations

import json
import shutil

from pytest_bdd import given, parsers, scenarios

from pubkit import __version__
from pubkit.conform import DENY_BASELINE, SHARED_IMPORTS

scenarios("conform.feature")

MANIFEST = '''[package]
name = "demo"
version = "0.1.0"
edition = "2021"

[lints.rust]
unsafe_code = "forbid"
missing_docs = "warn"
'''
LIB = '''//! A crate.

/// Longer documents.
pub mod _documentation {}
'''


def deny_toml() -> str:
    tables: dict[str, list[str]] = {}
    for (table, key), value in DENY_BASELINE.items():
        tables.setdefault(table, []).append(f'{key} = "{value}"')
    return "\n".join(f"[{t}]\n" + "\n".join(lines) + "\n" for t, lines in tables.items())


def vet_config(skip: str | None = None) -> str:
    imports = "".join(f'\n[imports.{name}]\nurl = "{url}"\n'
                      for name, url in SHARED_IMPORTS.items() if name != skip)
    return '[cargo-vet]\nversion = "0.10"\n' + imports


def workflow(version: str) -> str:
    return ("jobs:\n  ci:\n    uses: "
            f"schryer/pubkit/.github/workflows/rust-ci.yml@{version}\n")


@given("a Rust crate that meets the standard")
def conforming(repo, env):
    files = {
        "pubkit.json": '{"kind": "rust"}\n',
        "Cargo.toml": MANIFEST,
        "src/lib.rs": LIB,
        "SECURITY.md": "# Security\n",
        "testdata/cases.json": "[]\n",
        "fuzz/fuzz_targets/parse.rs": "",
        "deny.toml": deny_toml(),
        "supply-chain/config.toml": vet_config(),
        ".github/workflows/ci.yml": workflow(f"v{__version__}"),
    }
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    # The managed files, so `sync --check` gets as far as conform.
    from pubkit.cli import managed, render, templates, values
    every = templates("rust")
    for path in managed("rust"):
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(render(every[path], values({"kind": "rust"})))
    env["PUBKIT_TODAY"] = "2026-10-12"


@given(parsers.parse('the file "{path}" is removed'))
def file_removed(repo, path):
    (repo / path).unlink()


@given(parsers.parse('the directory "{path}" is removed'))
def directory_removed(repo, path):
    shutil.rmtree(repo / path)


@given("unsafe code is allowed")
def unsafe_allowed(repo):
    path = repo / "Cargo.toml"
    path.write_text(path.read_text().replace('unsafe_code = "forbid"', 'unsafe_code = "warn"'))


@given("missing docs are allowed")
def docs_allowed(repo):
    path = repo / "Cargo.toml"
    path.write_text(path.read_text().replace('missing_docs = "warn"\n', ""))


@given("the library has no _documentation module")
def no_documentation(repo):
    (repo / "src" / "lib.rs").write_text("//! A crate.\n")


@given("deny.toml allows yanked crates")
def yanked_allowed(repo):
    path = repo / "deny.toml"
    path.write_text(path.read_text().replace('yanked = "deny"', 'yanked = "warn"'))


@given(parsers.parse("the supply chain imports no audits from {name}"))
def no_import(repo, name):
    (repo / "supply-chain" / "config.toml").write_text(vet_config(skip=name))


@given(parsers.parse('the workflow calls rust-ci.yml at "{version}"'))
def pinned(repo, version):
    (repo / ".github" / "workflows" / "ci.yml").write_text(workflow(version))


@given(parsers.parse('an exception for "{rule}" until "{until}" because "{reason}"'))
def exception(repo, rule, until, reason):
    path = repo / "pubkit.json"
    config = json.loads(path.read_text())
    config.setdefault("exceptions", []).append({"rule": rule, "reason": reason, "until": until})
    path.write_text(json.dumps(config, indent=2) + "\n")
