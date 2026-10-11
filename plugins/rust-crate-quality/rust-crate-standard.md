# The Rust crate standard

What a crate published to crates.io from these repositories is held to. It
grew out of a study of BurntSushi's crates (regex, memchr, aho-corasick,
jiff, bstr, walkdir, csv) and was first applied to publet-core and
publet-algorithms. The rest of it was decided along the way: test vectors,
fuzzing, the supply-chain record, and version checks.

Each rule is marked:

- **[checked]** A tool enforces it in CI. An audit runs the tool and reports
  what it says, and does not judge the rule again by hand.
- **[judged]** Needs reading. An audit's real work is here.

## 1. Crate documentation, in `lib.rs`

The crate root is written in `lib.rs` as a `/*! ... */` block, with links to
items. It is never pasted from the README. In order:

1. **One sentence on what the crate is for**, then a runnable example of its
   central use. [judged]
2. **Overview:** the main types and functions, each linked, and the
   invariants they keep. A table works well for a crate of modules. [judged]
3. **A map of the rest of the page**, with links to its sections. [judged]
4. **Examples:** a cookbook of `## Example: <what it does>` blocks. They
   show edge cases and failures, not only the happy path: a rejected input,
   a boundary, a cycle, saturation. [judged]
5. **Why this crate?** What it does that a general-purpose crate would get
   wrong for this use. Say why, concretely. [judged]
6. **Related crates:** a table of crates doing similar jobs. Give the version
   checked and why this crate takes another approach. [judged] Every claim
   rests on a check that was actually run: an input tried, a root compared.
   A crate adopted rather than reimplemented is named too, as something this
   one builds on.
7. **Crate features:** every feature, or "None", said plainly. [judged]
8. **Minimum Rust version:** the `rust-version`, and the policy. It may rise
   in a release that bumps the second number (`y` in `x.y.z`, and in
   `0.y.z`), never in one that bumps only the third. [judged]
9. **Versioning:** Cargo's convention, and that the public API is checked
   against the last release. [judged]

Longer documents live in a `_documentation` module of empty submodules, each
`#[doc = include_str!("../X.md")]`, so they are versioned with the crate and
render on docs.rs: `testing`, `supply_chain`, `security`, `changelog`.
[checked: the build fails if a file is missing]

The README's examples are compiled as doctests through a `#[cfg(doctest)]`
item that includes the README, so they cannot rot. [checked]

## 2. Item documentation

- **Every public item is documented.** `#![deny(missing_docs)]` and
  `#![warn(missing_debug_implementations)]` are set. [checked]
- **Every public function has at least one example**, as a doctest. The
  repository's doc-coverage guard (`make doc-coverage-check`, where there is
  one) enforces it. [checked]
- Examples use `?`, with the error plumbing hidden:
  `# Ok::<(), Box<dyn std::error::Error>>(())`. [judged]
- Hidden setup lines (`# let ...`) are used sparingly. A reader must be able
  to tell where every name in a visible example comes from. [judged]
- **`# Errors`** says exactly when an error happens. Where that is a promise,
  it is part of the semver contract, so word it as one: "fails only if...".
  [judged]
- **`# Panics`** wherever something can panic. [judged]
- Domain sections where they matter: `# Complexity` for traversals and
  proofs, `# Platform behavior` when results could differ. [judged]
- Public error types are `#[non_exhaustive]`, or opaque structs with a
  `kind()`. An enum left exhaustive says why in its docs. [judged]
- Types of values that come from someone else (sizes, indexes and lengths
  read from the wire or a peer) do not depend on the target's word size. Use
  `u64`, not `usize`, so a 32-bit or `wasm32` machine can check anything a
  64-bit one can. Use `usize` only for what indexes this machine's memory.
  [judged]

## 3. Tests, and showing how each item is tested

- **Test vectors from independent sources** live in `testdata/` as JSON:
  RFCs, other implementations, published test suites. Every case names its
  source. They are read by `tests/vectors.rs`, and regenerated and compared
  in CI (`make vectors-check`). [checked where present; judged: are the
  sources really independent?]
- **Property tests** (proptest) cover invariants over many inputs:
  round-trips, and consistency at random sizes. [judged]
- **Oracles:** where an established crate implements the same standard, its
  results are compared with ours in the tests. It is a dev-dependency only.
  [judged]
- **Every test says what it covers** with a `// covers: module::item`
  comment. A test claims only items in its own module's area. [checked: the
  guard fails on a name that does not exist; judged: is the claim true?]
- **`TESTING.md`** is generated from those comments and the examples. It
  lists every public item with its examples and covering tests, and renders
  as `_documentation::testing`. Item docs do not link to tests. [checked:
  committed copy current]

## 4. Untrusted input

- Every function that parses or verifies something from outside (bytes,
  text, proofs, signatures) has a **cargo-fuzz target** in `fuzz/`. [judged:
  is any entry point missing a target?]
- Each input that ever found a bug is kept in `fuzz/regressions/<target>/`
  and replayed on every run. Each bug also gets a named regression test in
  the crate's own tests. [checked: `make fuzz-smoke`]
- Fuzzing runs on every change, briefly, and for longer on a schedule.
  [checked: the `fuzz` input of pubkit's `rust-ci.yml`]
- The crate forbids `unsafe` (`unsafe_code = "forbid"`) unless it needs it.
  A crate that uses `unsafe` also runs miri. [checked]

## 5. Packaging and metadata

- `Cargo.toml` states:
  - `description`, `license`, `repository`, `documentation`, `keywords` and
    `categories`;
  - an explicit `include` list;
  - `rust-version`;
  - `[package.metadata.docs.rs]` with `all-features = true` and
    `rustdoc-args = ["--generate-link-to-definition"]`.

  [judged]
- The `include` patterns are anchored with `/` (`"/README.md"`,
  `"/src/**/*.rs"`). Cargo reads them like gitignore patterns, so a bare
  `README.md` or `LICENSE` also matches any file of that name below, such
  as one in a Python test environment. `cargo package --list` shows
  exactly what ships. [judged]
- `make publish-check` runs `cargo publish --dry-run --locked` with a
  target directory of its own (`CARGO_TARGET_DIR=target/publish-check`).
  If the verify build shares `target/`, its dep-info names the packaged
  copy's sources. After that, cargo reports the crate up to date however
  `src/` changes, so tests run a stale binary. [judged]
- `license` names the licence, and the licence file is in the repository
  and ships in the package. Which licence is the project's choice; the
  standard asks only that it be stated and shipped. [checked: `make
  publish-check`]
- **Docs build as docs.rs builds them.** That means from the packaged
  crate, on nightly, with `--cfg docsrs` and the metadata's flags, with no
  warnings. [judged: run it before each release]
- The **README** is short and different from the crate docs:
  - a Documentation link that says what the docs cover;
  - Usage;
  - one example;
  - Minimum Rust version;
  - Security: how to report, and where the checks are described.

  [judged]

## 6. Versions, MSRV and the API

The make targets each kind of crate needs:

| Target | Library | Binary-only |
|---|---|---|
| `doc`, `lint`, `msrv`, `publish-check`, `supply-chain-check` | yes | yes |
| `doc-coverage-check` | yes | no |
| `vectors-check` | when it has test vectors | when it has test vectors |
| `fuzz-smoke` | when it reads untrusted input | when it reads untrusted input |


- Versions follow **Cargo's convention**. From 1.0: breaking is major, an
  addition is minor, a fix is a patch. Below 1.0 everything shifts one
  place: at `0.y.z`, breaking bumps `y` and anything compatible bumps `z`.
  [checked: pubrel]
- **The MSRV is tested.** CI builds the published crates on their
  `rust-version` (the `msrv` input), and the scheduled run tests the latest
  stable and beta. Raising the MSRV bumps the second number of the version,
  `y` in `x.y.z` and in `0.y.z`. [checked]
- **The public API is checked against the last release** by
  cargo-semver-checks, through pubrel (`"semver-checks": true` in
  release.json). An unrecorded break fails, below 1.0 too. [checked]
- **Changes are recorded** with `pubrel add` as they land. The CHANGELOG is
  generated from the package publet and never edited. A release may open
  with a summary (`pubrel summary`). [checked]

## 7. Supply chain and security

- `cargo deny` and `cargo vet` pass in CI. Each `cargo vet` exemption
  records the evidence that does exist. [checked]
- **`SUPPLY-CHAIN.md`** is generated per crate by `pubkit supply-chain`
  (`make supply-chain`, for the crates in `SUPPLY_CHAIN_CRATES`), from that
  crate's own dependency tree for any target, not the workspace's. It ships
  in the package, renders as `_documentation::supply_chain`, and marks
  build-time code. [checked:
  committed copy current]
- **`SECURITY.md`** says how to report a vulnerability (private reporting),
  what is checked and when, and what fuzzing has found. [judged]
- Releases carry a **signed security record** of the checks run, through
  pubrel's `security` section. [checked]

## 8. Cross-platform CI

Results that must be identical everywhere are tested on a 32-bit target and
a big-endian one: the `cross-targets` input, i686 and s390x. That is in
addition to Linux, macOS and Windows. [checked]

## 9. Crates that are command-line tools

A crate can be a tool and still be a crate. ripgrep is on crates.io, and
`cargo install` is how people get it. For a crate with only a binary target,
the rules above apply with these differences:

- **Its documentation is its README and its `--help`.** docs.rs renders no
  pages for a binary-only crate. The README carries what section 1 puts in
  the crate root:
  - what the tool is for, and its central use as a runnable command;
  - an overview of its commands;
  - why this tool;
  - related tools;
  - installation;
  - Minimum Rust version;
  - versioning.

  `--help` names every command and flag, and the README agrees with it.
  [judged]
- **Module docs still matter.** `//!` docs on `main.rs` and each module say
  what it does and why, for whoever maintains it. Section 2's rules apply
  to anything `pub` that another module relies on, with these changes:
  - `#![deny(missing_docs)]` checks nothing in a binary, because nothing is
    exported. Find undocumented items with
    `cargo clippy -- -W clippy::missing_docs_in_private_items`.
  - Doctests do not run in a binary target, so section 2's example rule
    does not apply. The README's example commands and the functional
    scenarios take their place.

  [judged]
- **Its versioned interface is what users depend on:**
  - commands and flags;
  - exit codes;
  - output meant for scripts;
  - the configuration files it reads.

  The README or a dedicated document names that interface. A change to it
  is recorded as `changed` or `removed`, like an API break.
  cargo-semver-checks does not apply, because there is no library API.
  [judged]
- **Its behaviour is tested end to end.** That means a functional suite
  (pubkit's Gherkin plugin) that runs the built binary as a user would, in
  addition to unit tests. [judged]
- **Its usage text is documentation.** The `--help` output, usually a
  string constant, is held to the same standard as the README. `--help`
  exits 0 and prints to stdout. [judged]
- **Metadata:** `documentation` points at the README in the repository.
  `[package.metadata.docs.rs]` is not needed, since docs.rs renders no
  pages for a binary. [judged]
- **`// covers:` comments** may name module-private items by their module
  path (`api::allowed`). `TESTING.md` and the doc-coverage guard are for
  libraries, and are not required of a binary. [judged]
- **Related tools** may include tools that are not Rust crates. Name the
  version or date checked, and how: their documentation, their source, or
  a run. [judged]
- **Every dependency comes from crates.io.** crates.io refuses a package
  with a git or path dependency. A tool built on a sibling crate is
  published after that crate. [checked: `cargo publish --dry-run`]
- Sections 4–8 apply unchanged:
  - untrusted input;
  - packaging;
  - versions and MSRV;
  - the supply chain;
  - cross-platform CI.

## 10. Writing

- Plain, specific sentences. Say what something does and why. No marketing,
  no "simply" or "just", no claims nobody checked.
- One idea per sentence. If a sentence needs a semicolon to carry two,
  split it.
- Name things exactly: the type, the function, the RFC section.
- Never describe something that is planned as done.

## Deliberately not adopted

These were considered and declined. Do not report their absence as a gap.

- Dual MIT/Unlicense as a requirement: each project chooses its licence.
- rustfmt at 79 columns.
- miri for crates that forbid `unsafe`.
- `no_std` until someone needs it.
- Benchmark harnesses until the crate makes performance claims.
- Links from item docs to their tests: `TESTING.md` does that job.
