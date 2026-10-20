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
pubkit init --kind=rust --bin=pub   # or --kind=utility, or --kind=service --project=NAME
pubkit lock                         # pin tests/requirements.txt, with hashes
pubkit sync --check                 # CI: fail if a managed file drifted
pubkit sync                         # after bumping pubkit: rewrite them
```

A repository is one of three kinds, named in `pubkit.json`:

| Kind | What it is |
|---|---|
| `rust` | A released Rust package, held to the Rust crate standard (below) |
| `utility` | A released tool or library in any language, or several, without the Rust package's extra checks |
| `service` | A private repository that deploys rather than releases, and runs its secret-needing commands through `./with-secrets` (below) |

The kind `python` (before 0.10.0) and `python-utility` (0.10.0) are now
`utility`: a package's language doesn't decide its kind. `sync` says so
when `pubkit.json` still names an old one.

Files are **managed**, owned by pubkit and rewritten by `sync`, or
**seeded**, written once by `init` and the repository's own after that.

- **Managed:** `tests/requirements-pubkit.txt` (the shared pins and the pubkit wheel). For a Rust package, also `pubkit.mk`, `rust-toolchain.toml`, and the crate standard with its agent (below). For a service, `with-secrets`.
- **Seeded:** `tests/requirements.txt` (includes the managed one), `tests/pytest.ini`, `.github/workflows/ci.yml`, for a Rust package a `Makefile` that includes `pubkit.mk`, and for a service `with-secrets.json`.

Extensions go in the seeded files, so nothing managed is ever edited by
hand. `sync --check` also fails when `tests/requirements.lock` pins another
pubkit, and in a Rust repository it runs `pubkit conform` (below).

## Services: `./with-secrets` and `service-ci.yml`

A service runs every command that needs a secret through `./with-secrets`:

```sh
./with-secrets terraform plan
./with-secrets scripts/setup-backup.sh
```

It reads a Bitwarden Secrets Manager project with the `bws` CLI, using a
read-only machine account's token (`BWS_ACCESS_TOKEN`, or that line in the
repository's git-ignored `.env`), and runs the command with the secrets in
its environment only: the calling shell never holds them, and no master
password is asked for. It stops, naming the projects the token can see, when
it cannot see the one configured. `with-secrets.json` is the repository's:

| Setting | Meaning |
|---|---|
| `project` | The project's name or id |
| `terraform.dir` | Where the Terraform files are |
| `terraform.secrets` | `declared`: a secret named like a declared variable in upper case (`HCLOUD_TOKEN` for `hcloud_token`) goes as `TF_VAR_hcloud_token` and under no other name. `map`: the names `terraform.names_from` lists go as `TF_VAR_secret_values` (declare it ephemeral) and `TF_VAR_secret_revisions` |
| `aliases` | Optional: files (paths from the repository root), each `{"aliases": {"NAME": "secret"}}`, that also export the secret named `secret` as `NAME`, for a tool that reads a fixed name (the Tailscale Terraform provider reads `TAILSCALE_OAUTH_CLIENT_ID`). Keep each file next to the code that needs it. An alias of a secret Bitwarden does not have is reported and skipped |

Every other secret goes under its name in upper case, `-` as `_`.

`service-ci.yml` is a service's CI: `pubkit sync --check` and shellcheck,
`terraform fmt`, `init -backend=false` and `validate` when `terraform-dir`
is given, and the repository's own tests when `test` is. No job has
secrets. Its `passed` job is the one check a ruleset would require; private
repositories on GitHub Free have no rulesets, so there it is a summary.

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

## Conformance, and declared exceptions

`pubkit conform` checks what no make target can, and `pubkit sync --check`
runs it in every Rust repository:

| Rule | Checked |
|---|---|
| RS-DOC-01 | each published library declares `pub mod _documentation` |
| RS-DOC-03 | each published library sets `missing_docs` in its lints |
| RS-SAFE-01 | each published crate sets `unsafe_code = "forbid"` |
| RS-TEST-01 | `testdata/` (or `crates/*/testdata/`) exists |
| RS-FUZZ-01 | `fuzz/fuzz_targets/*.rs` exists |
| RS-SUP-01 | `deny.toml` keeps the shared baseline, and `supply-chain/config.toml` imports the shared audit sets |
| RS-SUP-04 | `SECURITY.md` exists |
| RS-SUP-05 | the release key `release.json` names is labelled for what it releases ("pubunit release key"), not with a person's name: its label is public in every release it signs |
| RS-REL-01 | `release.json` declares `pubkit audit --check` as a security check |

It also checks that every workflow calling `rust-ci.yml` pins the pubkit
version the lock pins, so a repository's CI cannot fall behind the standard
its lock names. The published crates are those that do not set
`publish = false`, or the list `"published"` in `pubkit.json` gives.

A repository that cannot meet a rule yet declares an exception in
`pubkit.json`:

```json
{ "kind": "rust",
  "exceptions": [
    { "rule": "RS-TEST-01", "reason": "no independent source for the version arithmetic yet", "until": "2027-01-01" } ] }
```

An excepted rule's conformance checks are skipped, its make targets leave
`pubkit required`'s plan, and `pubkit conform` lists it as open. An
exception past its `until` date, or naming no rule, fails.

## A release cites a current audit

The judged rules need a reader, so a release needs a report by the
rust-crate-quality agent (RS-REL-01). For a release, the agent writes it to
`audit/<package>-<version>.md`, with a header:

```
---
package: pubunit
version: 0.1.1
standard: <pubkit audit --standard>
blocking: 0
date: 2026-10-13
auditor: rust-crate-quality
---
```

`pubkit audit --check [PACKAGE VERSION]` passes only on a report for that
release, made against the standard this pubkit ships, with no finding that
blocks a release. Without arguments it reads the release `pubrel prepare` is
cutting from `PUBREL_PACKAGE` and `PUBREL_VERSION`. A repository declares it
in `release.json`:

```json
{ "name": "quality audit current", "run": ".venv/bin/pubkit audit --check",
  "version": ".venv/bin/pubkit --version" }
```

`pubrel prepare` runs every declared check before it changes anything, so it
cuts no release without a current audit. It records each check's last line
in the release's signed security record, so the release cites its report.
Changing the standard makes earlier reports stale: a release after that
needs a new audit.

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

## Starting a package

    pubkit new --name=pubfoo --tag=PKG-PUBFOO-10-2026 [--description=TEXT]

In an empty repository, writes a crate that passes every check from its
first commit: the managed and seeded files, docs, test vectors, a fuzz
target, Gherkin scenarios, `deny.toml`, `supply-chain/`, `SECURITY.md`,
`release.json` with its security checks (the quality audit among them), and
a release key labelled "pubfoo release key", its secret in the gitignored
`corpus/.publet`. It refuses a `pubrel` older than 0.5.3 or a `pub` older
than 0.2.1, and prints the two steps it does not take itself: the lines
that add the package to `src/pubkit/protected.json` and `governed.json`.

## Protecting main

Every repository's default branch carries the same ruleset,
[`src/pubkit/ruleset.json`](src/pubkit/ruleset.json): it cannot be deleted
or force-pushed, it changes only by pull request (no approval needed), only
after the required checks pass, and nobody may bypass it.
[`src/pubkit/protected.json`](src/pubkit/protected.json) lists the
repositories and the checks each requires, the one thing that differs
between them: `ci / passed` from a pubkit workflow, `release / release`
where pubrel releases it, and `SonarCloud Code Analysis` once SonarQube
Cloud analyses its pull requests. Both ship in the wheel.

    pubkit protect [--check] [--config FILE | --require CHECK...] [REPO...]

creates the ruleset in each repository it names (by default every one
listed), or updates the ruleset already on the default branch to match,
renaming it. It calls the GitHub API through `gh`, signed in as the
repositories' owner, since only an admin may change a ruleset. `--check`
changes nothing: it prints how each repository differs, a setting GitHub
adds with its own default aside, and fails when one does.

The list is `--config FILE`, else a `protected.json` in the working
directory (the same shape), else the one the installed pubkit ships.
`--require CHECK`, repeated, reads no list: the repositories named require
those checks, as in `pubkit protect --require "ci / passed" schryer/newrepo`.

Installed as a tool, it runs from any directory and protects the
repositories its release lists:

    pipx install https://github.com/schryer/pubkit/releases/download/vX.Y.Z/pubkit-X.Y.Z-py3-none-any.whl
    pubkit protect

To change the protection, edit those two files and open a pull request. In
this checkout, `make protect` (or `make protect-check`, or `make protect
REPOS=schryer/recipes`) applies the edited files at once, making the venv
first if there is none; an installed pubkit applies them from the release
that carries them.

## The nightly

`.github/workflows/nightly.yml` runs every night over the repositories
`governed.json` lists. It reports, in its summary, the pubkit each pins,
whether it conforms to the latest standard, and its open exceptions
(`pubkit freshness`). Then it keeps one pull request per repository, on its
`pubkit-nightly` branch, with two commits (`pubkit nightly`):

1. every outdated pub* pin (`pubkit pins`): the latest pubkit, pubrel's
   release workflow and `pubrel-version`, `pub-version`, and the pub*
   crates in `Cargo.lock`;
2. the other crates' updates (`pubkit crates`) that `cargo vet` covers
   with an audit, adding no exemption, and that `cargo deny` passes. The
   body lists each update skipped with what `cargo vet suggest` says it
   needs.

Each commit regenerates `SUPPLY-CHAIN.md` and records an `internal` pubrel
row for each package it changes. An open pull request is updated, never a
second one opened, and left alone when the night brings nothing new. When
its CI fails, the pins are tested alone: if they pass, the crate commit is
dropped. Nothing merges it: the Sustenaut bot sends a Slack DM when it is
green or fails.

It needs three secrets in schryer/pubkit: `PUBKIT_BUMP_TOKEN`, a token
that may push branches and open pull requests in the governed repositories
(without it the nightly only reports); `SUSTENAUT_SLACK_BOT_TOKEN`, the
bot's token, with `chat:write`; and `SLACK_DM_USER_ID`, the Slack member
whom it DMs.

## Locking

`pubkit lock` resolves with pip's own resolver (`pip install --dry-run
--report`) and needs nothing else. The lock pins the pubkit wheel by URL and
sha256: a release carries the wheel as an asset, and `pip install
--require-hashes` verifies it.

## CI

```yaml
jobs:
  ci:
    uses: schryer/pubkit/.github/workflows/rust-ci.yml@v0.1.0      # or python-ci.yml, node-ci.yml
    with: { pub-version: v0.1.1 }
    secrets: inherit                                                # for SONAR_TOKEN
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

  On a pull request that touches no Rust code (`*.rs`), Cargo files (`Cargo.toml`, `Cargo.lock`, the toolchain pin), `fuzz/` or workflow files (`.github/workflows/`), the plan skips `fuzz` and `cross`, the slow emulated jobs: a documentation or `pubkit.json` change does not wait for them. A release pull request (a `release/` branch), a push to main and the nightly run everything. `pubkit required --event E --head-ref B --changed-files F` decides, and says why on stderr.

  The `gates` and `functional` inputs can only add targets. The `msrv` and `fuzz` inputs are ignored: those jobs run because the standard requires them. Every job reports through `passed`, the one check a ruleset needs.
- **`python-ci.yml`:** takes its commands as inputs (`setup`, `test`, `browser-test`), so a repository with its own entry point keeps it. A non-empty `browser-test` adds a job that installs Playwright's Chromium.
- **`node-ci.yml`:** for a Node package. It installs from `package-lock.json` with `npm ci --ignore-scripts`, so no dependency runs code by being installed, checks pubkit's managed files from `tests/requirements.lock`, and runs `npm test`. A non-empty `browser-test` adds a job that installs Playwright's Chromium with `npx --no-install`, which runs only what the lock installed. The commands are inputs (`setup`, `test`, `browser-test`, `browser-setup`). `pubkit init --kind=utility` seeds a `ci.yml` calling it in a repository with a `package.json` and no `pyproject.toml`.

### SonarQube Cloud

`rust-ci.yml`, `python-ci.yml` and `node-ci.yml` each run a `sonar` job, part of `passed`. Given a `SONAR_TOKEN` secret (pass `secrets: inherit`), it sends the code to SonarQube Cloud as the project `OWNER_REPO` in the organization `OWNER`, with the full history so it can tell new code from old; `sonar-args` adds properties. Without a token, as in a private repository or a pull request from a fork, it passes with a notice. To turn it on for a public repository:

1. In SonarQube Cloud, analyse the repository as a new project in the organization named like its GitHub owner, keeping the key `OWNER_REPO`.
2. Under the project's Administration, Analysis Method, turn Automatic Analysis off: the two cannot run together.
3. Add the token as the repository's `SONAR_TOKEN` Actions secret.

The default quality gate asks for 80% coverage on new code, and a scan without a coverage report counts it as 0%, so each workflow hands the sonar job one:

| Workflow | Report | Written by |
|---|---|---|
| `rust-ci.yml` | `coverage/lcov.info` | `make coverage`, which the functional job runs in place of `make functional` when there is a token: the Rust tests and the Gherkin suite on a build cargo-llvm-cov instruments (`cargo-llvm-cov-version`) |
| `python-ci.yml` | `coverage-report`, by default `coverage/coverage.xml` (Cobertura) | the `test` command, such as coverage.py with `patch = ["subprocess"]`, which also measures the programs the suite starts (pubkit's own `make functional` does this) |
| `node-ci.yml` | `coverage-report`, by default `coverage/lcov.info` | the `test` command, such as `node --test --experimental-test-coverage --test-reporter=lcov --test-reporter-destination=coverage/lcov.info` |

A test command that writes no report leaves the scan as it was.

Every action these workflows use is pinned to a commit, with its version in a comment, and a test fails on one that is not.

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
