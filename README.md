# pubkit

The testing and quality setup every package shares:
- a pytest-bdd plugin;
- the files each repository must hold, kept in step by a command;
- reusable CI workflows;
- the Rust crate standard, with a Claude Code agent that audits crates
  against it.

Every package tests its behaviour the same way: Gherkin feature files, run
by pytest-bdd, driving the built program as a user would. pubkit is that
setup, written once.

## The pytest plugin

Installed, it loads into every pytest session (the `pytest11` entry point):

| Provides | What it is |
|---|---|
| `binary(name, var=None)` | The program under test, from `<NAME>_BIN_DIR` (`pub` → `PUB_BIN_DIR`). Never from PATH, so the suite cannot quietly test whatever is installed; another implementation runs the suite by setting one variable. |
| `run(program, *args, stdin=, cwd=, env=)` | Runs a command in the test's scratch directory and returns a `Completed`: `code`, `stdout`, `stderr`, `lines`. |
| `ctx` | A per-scenario namespace. `ctx.cleanup(fn)` runs `fn` when the scenario ends, in reverse order, passed or failed. |
| common steps | `it succeeds`, `it fails`, `the exit code is N`, `it prints "…"`, `stderr mentions "…"`, `stdout is empty`. Each reads the `result` fixture a `when` step sets with `target_fixture="result"`. |
| markers | Every `@tag` in the feature files is registered, so `--strict-markers` works and `-m "not browser"` selects. |

A suite's own step with the same text wins over the plugin's.

## Keeping the files in step

```sh
pubkit init --kind=rust --bin=pub   # or --kind=python
pubkit lock                         # pin tests/requirements.txt, with hashes
pubkit sync --check                 # CI: fail if a managed file drifted
pubkit sync                         # after bumping pubkit: rewrite them
```

Files are **managed**, owned by pubkit and rewritten by `sync`, or
**seeded**, written once by `init` and the repository's own after that.

- **Managed:** `tests/requirements-pubkit.txt` (the shared pins and the pubkit wheel). For Rust, also `pubkit.mk`, `rust-toolchain.toml`, and the crate standard with its agent (below).
- **Seeded:** `tests/requirements.txt` (includes the managed one), `tests/pytest.ini`, `.github/workflows/ci.yml`, and for Rust a `Makefile` that includes `pubkit.mk`.

Extensions go in the seeded files, so nothing managed is ever edited by
hand. `sync --check` also fails when `tests/requirements.lock` pins another
pubkit.

## The supply-chain report

`pubkit supply-chain CRATE...` writes each crate's `SUPPLY-CHAIN.md` beside
its manifest, from the repository's `cargo vet` files (`supply-chain/`).
The report lists every crate it builds, for any target, and marks build
scripts and procedural macros. It says how each is vouched for: an imported
audit, a trusted publisher, or an exemption with its evidence. `pubkit.mk`
runs it as `make supply-chain`, and `make supply-chain-check` fails when a
committed report is stale. Set `SUPPLY_CHAIN_CRATES` in the Makefile to the
crates that are published.

## The checks the standard requires

Every `[checked]` rule in the Rust crate standard has a stable ID and names
what checks it: `[checked: RS-DOC-04, make doc-coverage-check, library]`.
`make standard` generates `standard.json` from those tags, and it ships in
pubkit. `pubkit required` reads it, and the repository's crates, and prints
the make targets each `rust-ci.yml` job must run:

```sh
$ pubkit required
gates=fmt-check lint doc doc-coverage-check publish-check
functional=functional vectors-check supply-chain-check
msrv=msrv
fuzz=fuzz-smoke
audit=deny vet
cross_job=true
...
```

A command-line tool, with no library target, is not held to the library
rules (`doc-coverage-check`). `pubkit.mk` provides every target the
standard names. A repository sets the variables they read in its Makefile:

| Variable | Default | Read by |
|---|---|---|
| `PUBLISHED_CRATES` | `SUPPLY_CHAIN_CRATES` | `doc-coverage`, `publish-check`, `msrv` |
| `VECTORS_GENERATE` | `./tools/vectors/generate.py` | `vectors`, `vectors-check` |
| `VECTORS_DIRS` | `testdata` and `crates/*/testdata`, where present | `vectors-check` |
| `FUZZ_TARGETS` | every `fuzz/fuzz_targets/*.rs` | `fuzz-smoke` |
| `MSRV` | the first `rust-version` in `Cargo.toml` | `msrv` |

`pubkit doc-coverage [--check] [CRATE...]` writes each published library's
`TESTING.md`: every public item, the examples its docs run, and the tests
whose `// covers:` comment names it. With `--check`, it fails on a public
function with no example, a `// covers:` naming no public item, or a stale
`TESTING.md`. It moved here from publet's `tools/`, so every repository runs
the same version.

## The Rust crate standard, and an agent that audits against it

[**The Rust crate standard**](plugins/rust-crate-quality/rust-crate-standard.md)
is what a crate published from these repositories is held to, whether a
library or a command-line tool. It covers:
- crate and item docs in the style of BurntSushi's crates;
- test vectors and `// covers:` links;
- fuzzing and packaging;
- Cargo's version convention, with API checks;
- the supply-chain record;
- cross-platform CI;
- plain writing.

Each rule is marked *checked* (a tool enforces it) or *judged* (it needs
reading). The standard ends with what was deliberately not adopted.

The **`rust-crate-quality` agent** audits a crate against that standard, and
writes or fixes its docs to meet it. pubkit's repository is a Claude Code
plugin marketplace, so the agent installs once for every repository, and
none of them needs a `.claude/` folder. In Claude Code:

```
/plugin marketplace add schryer/pubkit
/plugin install rust-crate-quality@pubkit
```

Then ask for it by name, in any repository:

- "Use rust-crate-quality to audit publet-core." It returns a report and
  edits nothing.
- "Use rust-crate-quality to fix the docs of pubrel." It edits
  documentation, then re-runs the checks.

Claude Code also offers it unprompted when you ask whether a crate is ready
to publish.

What it does:

1. Reads the standard. It ships with the agent, so the agent never works
   from memory.
2. Runs the checks the repository has (`make doc`, `lint`, `msrv`,
   `publish-check`, `doc-coverage-check` and the like) and reports what they
   say. It doesn't judge those rules again.
3. Builds the docs as docs.rs will: from the packaged crate, on nightly,
   with no warnings.
4. Reads the docs and code for the judged rules. It verifies claims, such
   as each "Related crates" row, rather than trusting them.
5. Reports each finding with its rule, file and line, from what blocks a
   release down to what is worth considering.

**What it changes:** documentation only. That means doc comments, the
README and `SECURITY.md`. Anything that needs a code change, such as
`#[non_exhaustive]` or a `u64` for a peer-supplied size, comes back as a
finding for you to decide.

**What it never does:** edit generated files, which it regenerates instead,
or record the change. Commit its edits as usual, with `pubrel add internal
"..."`.

`/plugin marketplace update pubkit` picks up a newer standard.

## Locking

`pubkit lock` resolves with pip's own resolver (`pip install --dry-run
--report`) and needs nothing else. The lock pins the pubkit wheel by URL and
sha256: a release carries the wheel as an asset, and `pip install
--require-hashes` verifies it.

## CI

```yaml
jobs:
  ci:
    uses: schryer/pubkit/.github/workflows/rust-ci.yml@v0.1.0      # or python-ci.yml
    with: { pub-version: v0.1.1 }
  release:
    needs: ci
    uses: schryer/pubrel/.github/workflows/release.yml@v0.1.0
    with: { pub-version: v0.1.1, pubrel-version: v0.1.0 }
    permissions: { contents: write }
```

- **`rust-ci.yml`:** runs every check the standard requires. A `plan` job builds the hash-locked venv, runs `make sync-check`, and asks `pubkit required` which make targets each job runs. Then:
  - `gates` runs the required gate targets, then any the `gates` input adds;
  - `test` runs `cargo test` on Linux, macOS and Windows (`test-args` adds arguments);
  - `functional` runs the required functional targets, then any the `functional` input adds, optionally with extra apt packages and a released `pub`;
  - `msrv` runs `make msrv MSRV=<the lowest rust-version stated>` on that toolchain;
  - `fuzz` runs `make fuzz-smoke` on nightly (`fuzz-seconds`, and `fuzz-seconds-scheduled` on a schedule);
  - `cross` runs `cargo test` under qemu on `cross-targets`: i686 and s390x by default, or aarch64 `-unknown-linux-gnu`, with `cross-test-args`;
  - `audit` runs `make deny` and `make vet`, with `cargo-deny-version` and `cargo-vet-version`;
  - when the caller also runs on a `schedule`, those runs test on the latest stable and beta Rust (`scheduled-toolchains`).

  The `gates` and `functional` inputs can only add targets. The `msrv` and `fuzz` inputs are ignored: those jobs run because the standard requires them. Every job reports through `passed`, the one check a ruleset needs.
- **`python-ci.yml`:** takes its commands as inputs (`setup`, `test`, `browser-test`), so a repository with its own entry point keeps it. A non-empty `browser-test` adds a job that installs Playwright's Chromium.

## Developing

```sh
make venv     # hash-locked environment, this checkout installed editable
make check    # standard.json current, then the Gherkin suite
```

The suite drives the plugin through pytest's `pytester`. Each scenario
builds a throwaway suite, runs it in a subprocess, and judges what pytest
reports. It drives the command in a scratch repository. pubkit is its own
first consumer: its CI runs through `python-ci.yml` from this checkout, and
it is released with [pubrel](https://github.com/schryer/pubrel). Its
package publet, `pkg.pubkit`, lives in `corpus/`.

## Licence

Apache-2.0: see [`LICENSE`](LICENSE).
