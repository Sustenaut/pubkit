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
| `service` | A repository that deploys rather than releases, and runs its secret-needing commands through `./with-secrets` (below) |

The kind `python` (before 0.10.0) and `python-utility` (0.10.0) are now
`utility`: a package's language doesn't decide its kind. `sync` says so
when `pubkit.json` still names an old one.

Files are **managed**, owned by pubkit and rewritten by `sync`, or
**seeded**, written once by `init` and the repository's own after that.

- **Managed:** `tests/requirements-pubkit.txt` (the shared pins and the pubkit wheel), `pubkit` (below), and `AGENTS.md`, the working rules agents and people follow in every repository, starting with when a pull request is draft (below). For a Rust package, also `pubkit.mk`, `rust-toolchain.toml`, and the crate standard with its agent (below). For a service, `with-secrets`.
- **Seeded:** `tests/requirements.txt` (includes the managed one), `tests/pytest.ini`, `.github/workflows/ci.yml`, for a Rust package a `Makefile` that includes `pubkit.mk`, and for a service `with-secrets.json`.
- **Healed:** `.gitignore` is the repository's own, but it must ignore `.env`, where local tools read secrets (`./with-secrets` its Bitwarden token). `init` and `sync` append `.env` when it's missing, with a comment above it, and leave the rest of the file as it is; `sync --check` fails until it's there. A pattern that already covers it, such as `/.env` or `.env*`, counts, and a later `!.env` takes it back.

Extensions go in the seeded files, so nothing managed is ever edited by
hand. `sync --check` also fails when `tests/requirements.lock` pins another
pubkit, and in a Rust repository it runs `pubkit conform` (below).

Run as any release but the one `tests/requirements.lock` pins, pubkit
refuses the commands that judge or write the repository by its standard
(`sync`, `lock`, `supply-chain`, `doc-coverage`, `required`, `conform`,
`audit`, `record`) and says to run `make venv`, use `./pubkit`, or, when
it is the newer one, `pubkit bump`. `PUBKIT_ANY_VERSION=1` runs it anyway.
In a Rust repository, each `pubkit.mk` target that runs a tool from `.venv`
rebuilds `.venv` first when the lock has changed since `make venv` last
ran, so a moved pin never runs the old pubkit.

**`./pubkit`** runs the pubkit this repository pins, whichever (if any) is
installed: `./pubkit sync --check`. It
runs `uvx` on that release's wheel, which uv fetches once and caches, in
the repository's root, from whichever directory it is called. Without uv
it says how to install it. It is managed, so `pubkit sync` moves it with
the rest when a repository moves to a new pubkit.

## Services: `./with-secrets` and `service-ci.yml`

A service runs every command that needs a secret through `./with-secrets`,
which reads its Bitwarden project, as `with-secrets.json` says, and runs the
command with the secrets in its environment only:

```sh
./with-secrets terraform plan
./with-secrets scripts/setup-backup.sh
```

That script, managed in every repository with a `with-secrets.json`, is one
line: `./pubsec run --cwd "$PWD" -- COMMAND`. Holding a secret's value is
[pubsec](https://github.com/Sustenaut/pubsec)'s, not pubkit's, so `pubkit
sync` writes `./pubsec` beside it, which runs the repository's pubsec
(`deploy.providers`' entry, or pubkit's floor where a repository has no
deploy block), its wheel checked against its sha256 on every run. pubsec's README, "Services:
./with-secrets", explains `with-secrets.json` and `pubsec run`.

`service-ci.yml` is a service's CI: `pubkit sync --check` and shellcheck,
`terraform fmt`, `init -backend=false` and `validate` when `terraform-dir`
is given, the repository's own tests when `test` is, and SonarQube Cloud's
scan (below). No job but the scan has secrets. Its `passed` job is the one check a ruleset would require; private
repositories on GitHub Free have no rulesets, so there it is a summary.

### Sending secrets to a repository

`pubkit.json`'s `github_app` and `secrets` blocks name the values a
repository needs from Bitwarden and where each goes on GitHub, and `pubkit
sync --check` refuses one it can't use. Sending them is pubsec's:
`./with-secrets ./pubsec app-secrets [--check]` (pubsec's README, "Sending
secrets to a repository"). `pubkit app-secrets` says so, and is removed in
the next minor.

### Creating a GitHub App

`./pubsec github-app create MANIFEST` makes an app with GitHub's manifest
flow and puts its three values in Bitwarden, where `./pubsec app-secrets`
finds them (pubsec's README, "Creating a GitHub App"). `pubkit github-app
create` says so, and is removed in the next minor.

### Making a service account key

`./pubsec service-account-key create` makes a Google Cloud service account
key and writes it to Bitwarden from memory (pubsec's README, "Making a
service account key"). `pubkit service-account-key create` says so, and is
removed in the next minor.

## Hosting

A repository's hosting is [pubhost](https://github.com/Sustenaut/pubhost)'s: the
Terraform modules that turn `pubkit.json`'s `hosting` block into DNS, mail,
DNSSEC, Cloud Run and bucket resources, `terraform.yml`, which applies a stack
from CI, the `deploy-static` and `docker-build` actions, and `pubhost dns check`
and `pubhost site check`. Each is used from there by commit:
`Sustenaut/pubhost/actions/docker-build@<commit> # vX.Y.Z`,
`source = "github.com/Sustenaut/pubhost//terraform/hosting?ref=<commit>" # vX.Y.Z`.
pubhost's README describes every setting of the hosting block.

pubkit keeps the block's shape: `pubkit sync` (and `sync --check`, in CI) checks
it (`src/pubkit/hosting.py`) and stops on anything it does not know, since
Terraform drops a setting its types do not name. A new provider type is learnt
here first, then by pubhost's modules. pubkit also keeps
`job-service-terraform.yml`, which checks a stack without a backend, and the
`ci / site` job ("Generated CI" below).

## pubkit's actions

- **`actions/cache`:** keeps what a build fetched or made between runs,
  by the entries of pubkit.json's `cache` block (see "Caching a build"
  below). Call it with `step: restore` before the build and `step: save`
  after it, which also drops what no run used; `fresh: true` restores no
  fetched entry, and `refresh: true` restores no entry at all.
- **`actions/pr-label`:** says whether the pull request a run is for
  carries a label, `refresh cache` by default (`label` names another),
  as its output `present`. The pull request is the event's own, the one a
  merge queue group is for, or, on a push, the ones merged into the pushed
  commit, which needs `pull-requests: read`. One label a person puts on a
  pull request can so steer what its deploy does once it merges, such as
  rebuilding everything:

  ```yaml
  permissions: { contents: read, pull-requests: read }
  steps:
    - id: refresh
      uses: Sustenaut/pubkit/actions/pr-label@<commit> # vX.Y.Z
    - uses: Sustenaut/pubkit/actions/cache@<commit> # vX.Y.Z
      with: { step: restore, refresh: "${{ steps.refresh.outputs.present }}" }
  ```
- **`actions/retry`:** runs a bash script (`run`) with `bash -ec` up to
  `tries` times (3), for a step a flaky download can fail. After each
  failed try but the last it warns "`<what>` failed (attempt N of T)"
  (`what` is `setup` by default) and waits `pause` seconds (15); it fails
  when every try did. The inputs reach the script through its
  environment, so the script runs exactly as given.
- **`actions/terraform-init`:** `terraform -chdir=<dir> init <args>`
  (`dir` `.`, `args` `-input=false`, `terraform` the binary), tried as
  retry tries a script, then once more with every provider from
  pubkit.json's `mirror.terraform`, when it names one (see "A wheel
  mirror"); the lock's hashes still check what the mirror gives.

  ```yaml
  - uses: hashicorp/setup-terraform@<commit> # vX.Y.Z
  - uses: Sustenaut/pubkit/actions/terraform-init@<commit> # vX.Y.Z
    with: { dir: infra, args: -backend=false -input=false }
  ```

pubkit's own reusable workflows run their setup and terraform init through
these two, pinned by a release's commit as any caller pins them: a reusable
workflow runs in the caller's checkout, where `./actions/` is the caller's.
So a change to either action reaches the workflows one release later, when
their pins move to the release that has it.

## Deploying a service to k3s

The deploy walk is [pubdeploy](https://github.com/Sustenaut/pubdeploy)'s:
`k8s-deploy.yml`, the kustomize component `k8s/components/app`, `pubdeploy k8s
preflight` and `diagnose`, and the scheduled `deploy-check.yml`. A caller pins
`Sustenaut/pubdeploy/.github/workflows/k8s-deploy.yml@<commit> # vX.Y.Z`;
pubdeploy's README, "Deploying a service to k3s", says what it takes.
`actions/deploy-ssh` is pubdeploy's too.

## Declaring what a service needs

pubkit.json's `deploy` block says what a service needs before it deploys,
and `./pubdeploy walk` checks and makes it (pubdeploy's README, "Declaring
what a service needs", "Making what a service needs" and "Provider
contract"). pubkit keeps three things of it:

- **`deploy.providers`** lists pubsec and pubdeploy, each at the oldest
  release this pubkit works with or newer (`PUBSEC_FLOOR` and
  `PUBDEPLOY_FLOOR` in `new.py`, which its suite tests): `pubkit sync` adds
  the floor where none is listed and moves one below it up, changing nothing
  else of pubkit.json, and `sync --check` fails without one or below it. The
  floor moves only when pubkit needs a newer release.
- **The launchers.** In a repository with a deploy block, `sync` writes
  `./pubdeploy`, which runs the listed pubdeploy with every other
  `deploy.providers` wheel, and `./pubkit` runs with every wheel, pubdeploy's
  too, each fetched once into the user's cache and checked against its
  sha256 on every run.
- **The hooks.** `pubkit sync` calls each `sustenaut.sync` hook,
  `check(root, config, check) -> (lines, failed)`, after its own work, and
  `--check` fails when one says so; `pubkit bump` calls each
  `sustenaut.bump` hook, `bump(root) -> lines`, before it writes the
  launchers. pubdeploy's are the deploy block's offline check and the
  scheduled walk's caller, and moving deploy.providers and the uses of
  pubdeploy's workflows to their latest releases. A hook that fails is said
  by its name and its error's type alone. With a deploy block and no
  pubdeploy installed, `sync --check` fails, saying so: CI's environment
  installs it from the lock, since `tests/requirements-pubkit.txt` lists
  every deploy.providers wheel, and `./pubkit` runs with each.

## Terraform from CI

`terraform.yml`, the reusable workflow that applies a stack from the default
branch, is [pubhost](https://github.com/Sustenaut/pubhost)'s
(`Sustenaut/pubhost/.github/workflows/terraform.yml@<commit> # vX.Y.Z`).

## Caching a build

A build that downloads slow-changing data, or makes many outputs one by
one, can keep both between runs by entries the repository states in
pubkit.json. The cache block is one map of named entries, each with the
same keys:

```json
"cache": {
  "data": { "path": "data", "refresh": "week" },
  "packs": { "path": ".packcache", "unit": "data/areas/{unit}/",
             "inputs": ["data/boundaries.geojsonl", "mapbuild/*.py"],
             "tools": ["tippecanoe --version"] },
  "overview": { "path": ".packcache/overview",
                "inputs": ["data/national/", "data/boundaries.geojsonl", "mapbuild/*.py"],
                "tools": ["tippecanoe --version"] }
}
```

| Key | What |
|---|---|
| `path` | the file or directory kept between runs |
| `refresh` | `run`, `day`, `week` (ISO week), `month` or `never` |
| `version` | a free string; changing it fetches anew |
| `inputs` | files, directories or globs the key depends on |
| `tools` | commands whose output counts, such as `tippecanoe --version` |
| `unit` | one pattern with `{unit}`, filled from `--unit`, so each unit gets its own fingerprint |

An entry needs `refresh` or `inputs` (or both). An unknown key fails
`pubkit sync`, naming it. Each entry gets its own cache key, and a block
holds at most eight entries.

- A **fetched** entry (`refresh` other than `run`, and no `inputs` or
  `unit`) is kept for its period: the first run of a period fetches it,
  and later runs restore it. A fresh run restores none.
- A **made** entry (`inputs`, `unit` or `refresh: run`) is restored from
  the latest run. The build makes it through

  ```sh
  pubkit cache run packs --unit north/alpha --output pack.pmtiles -- bin/build-pack ...
  ```

  which fingerprints the unit (the entry's name, the unit, the `unit`
  pattern filled in, each `inputs` entry, each `tools` command's output,
  and the version), copies the outputs when that fingerprint was made
  before, and otherwise runs the command and keeps what it wrote. It
  prints `reused packs north/alpha` or `built packs north/alpha`; the
  command's own output goes to stderr.

In a workflow, `actions/cache` restores before the build and saves after
it. Saving first drops what no run used, so a made entry keeps one
build's worth (`pubkit cache prune` does the same by hand). With
`refresh: true` (`pubkit cache plan --refresh`) the restore step restores
nothing, so the build fetches and makes everything anew; `actions/pr-label`
sets it from a `refresh cache` label on the pull request.

```yaml
- uses: Sustenaut/pubkit/actions/cache@vX.Y.Z
  with: { step: restore, fresh: "${{ inputs.fresh || false }}" }
- run: bin/build
- uses: Sustenaut/pubkit/actions/cache@vX.Y.Z
  with: { step: save }
```

## Checking a public site

`pubhost site check` and its WEB- rules are
[pubhost](https://github.com/Sustenaut/pubhost)'s, and its `deploy-static` runs
it before every deploy. Their exceptions are declared in `pubkit.json`'s
`exceptions`, beside the Rust standard's, which `pubkit conform` reads alone.

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
`TESTING.md`. It lives here so every repository runs
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
| RS-SUP-05 | the release key `release.json` names is labelled for what it releases ("example-crate release key"), not with a person's name: its label is public in every release it signs |
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
package: example-crate
version: 0.1.1
standard: <pubkit audit --standard>
blocking: 0
date: 2026-10-13
auditor: rust-crate-quality
---
```

`pubkit audit --check [PACKAGE VERSION]` passes only on a report for that
release, made against the standard this pubkit ships, with no finding that
blocks a release and no rule left unjudged. Without arguments it reads the release `pubrel prepare` is
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

### Preparing the report

```sh
./pubkit audit prepare --package example-crate        # audit/example-crate-<next>.md
with-secrets ./pubkit audit draft --package example-crate   # optional: Claude drafts the judged rules
```

`pubkit audit prepare --package NAME` writes the report for the release
`pubrel next --package NAME` gives (or `--version`). It decides every rule a
machine can decide, and leaves the rest for a person:

- **The header**, as above, adds `commit` (the commit audited) and
  `last-release` (the package's last release tag).
- **Each finding** goes under its severity, with its rule, file and line.
  Builds and lints:
  - `cargo doc` and `cargo clippy` warnings for the package, and its
    doctests;
  - a library's docs built from the packaged crate on nightly, as docs.rs
    builds them;
  - `cargo publish --dry-run`, and the build on the package's
    `rust-version`;
  - the make targets the standard names: `doc-coverage-check`,
    `supply-chain-check`, `vectors-check`, `deny` and `vet`.

  The docs and the manifest:
  - public functions with no example, or returning a `Result` with no
    `# Errors`;
  - examples that unwrap, or use `?` without the hidden `Ok`;
  - public error enums that are not `#[non_exhaustive]`;
  - crate-root sections that are missing or out of order, and
    features, MSRV and versioning against `Cargo.toml` and `release.json`;
  - each "Related crates" row's version against `Cargo.lock`;
  - the README's sections, the `Cargo.toml` metadata, and unanchored
    `include` patterns;
  - each tool `SECURITY.md` names, against the workflows that run it and
    when, and its imported audit sets against `supply-chain/config.toml`;
  - for a command-line tool, `--help` run: does it exit 0 to stdout?
- **Each rule that needs judgement** is under "Needs review", marked
  `[needs review]`, with what the machine found for it. Examples: the
  hidden setup lines, the functions that can panic, `usize` in
  signatures, the Related crates rows, the test vectors' sources, the
  entry points beside the fuzz targets, and the sentences section 10
  rules out.

The judged rules have no IDs in the standard. `standard.json` keys them by
section and place (`1.6` is Related crates), which holds while the
standard's text does, and a report names that text's sha256. `--check`
fails while any `[needs review]` mark remains. A person judges each rule,
moves what they find into the sections above, counts `blocking` again, and
removes the mark. `--quick` skips what takes minutes (the docs.rs, MSRV and
publish builds, and the make targets). An existing report is written over
only with `--force`, since it may hold a review.

`pubkit audit draft --package NAME` asks Claude to draft those rules. It
sends the Messages API:

- the package's diff since its last release tag, without lockfiles and
  generated files;
- the rules still marked, each with what the machine found;
- the last release's report, when there is one.

It asks for JSON matching a schema: per rule a verdict, findings with file
and line, and the reasoning. It merges each draft into the report, marked
`[machine-drafted by MODEL: needs human review]`, and adds the drafted
findings that block to `blocking`. `--check` refuses that mark too, so a
person still reads every draft. The key is `ANTHROPIC_API_KEY`: run it
through a `with-secrets` whose Bitwarden project holds a secret of that
name, or export it. The model is `--model`, `PUBKIT_AUDIT_MODEL`, or
`claude-opus-5-5`. `ANTHROPIC_BASE_URL` points it elsewhere, as the tests
do. A refusal, an error or an answer cut short writes nothing.

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
/plugin marketplace add Sustenaut/pubkit
/plugin install rust-crate-quality@pubkit
```

Then ask for it by name, in any repository:

- "Use rust-crate-quality to audit example-crate." It returns a report and
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
`corpus/.publet`. Its repository is `--owner`'s, by default the owner the
`origin` remote names; without either it stops. It refuses a `pubrel` older
than 0.5.3 or a `pub` older than 0.2.1, and prints the steps it does not take
itself: the protect block to add to `pubkit.json`, and switching the repository
on in the release app.

## Backing up signing keys

A repository names every signing key it uses, and where it is backed up, in
`pubkit.json`:

```json
"signing_keys": [
  {"path": "corpus/.publet/signing.key",
   "bitwarden": {"project": {"id": "PROJECT-UUID", "name": "Pub"},
                 "name": "pub-signing-key-example"}}
]
```

The project is found by its `id`; `name` is a label for people, and a run
warns when Bitwarden's name for the project has changed. A plain string is
taken as an id when it is a UUID, else as a name, in any case.

`path` is from the repository's root, or from the home directory when it
starts with `~/`: a key kept outside any repository, such as one under
`~/.config`, is declared by the repository whose tools use it. `pubkit sync
--check` refuses a block it can't use.

Backing them up is pubsec's: `./pubsec signing-key backup [--under DIR]
[--dry-run]` writes each declared key on this machine that its project lacks,
from memory, never overwriting one (pubsec's README, "Backing up signing
keys"). `pubkit signing-key backup` says so, and is removed in the next minor.

## Protecting main and the release tags

Every repository's default branch carries the same ruleset,
[`src/pubkit/ruleset.json`](src/pubkit/ruleset.json), which ships in the
wheel: it cannot be deleted or force-pushed, it changes only by pull
request (no approval needed), merged by GitHub's merge queue, and nobody
may bypass it. "Merge when ready" puts a pull request in the queue, which
merges it into main as it stands then, together with the pull requests
ahead of it, runs the required checks on that merge (the `merge_group`
event, which the CI `pubkit sync` writes runs on), and merges only when
they pass. So the tree the checks passed is the tree main holds after the
merge, the run on main repeats the queue's, and tagging a release needs no
tests of its own, yet nobody has to bring a branch up to date by hand:
pull requests stacked behind one another merge one at a time. GitHub
offers a merge queue only to an organization's public repositories (and to
private ones on Enterprise Cloud); a repository without one, `"queue":
false` in its entry or `--no-queue`, instead merges only after the checks
pass on a branch that is up to date with main. `pubkit protect` works this
out itself: it reads each repository's owner and visibility (and a private
one's organization plan) and leaves the queue out, with a note, for a
personal account's repository or a private one off Enterprise Cloud, and
should GitHub still refuse the `merge_queue` rule, it warns and tries once
more without it. The generated `pr.yml`'s `merge_group` trigger then never
fires, and does no harm. Which repositories carry it, and the checks each
requires, is local configuration, not part of pubkit:

    pubkit protect [--check] [--no-tags] [--no-queue] [--repos FILE | --require CHECK...] [REPO...]

creates the ruleset in each repository, or updates the ruleset already on
the default branch to match, renaming it. It does the same for the release
tags' ruleset, [`src/pubkit/tag-ruleset.json`](src/pubkit/tag-ruleset.json):
nobody may create, move or delete a `v*` tag, and only GitHub Actions may
bypass it. GitHub refuses the GitHub Actions app as a bypass actor in an
organization's repository, so such a repository names its release GitHub App
with `"release_app": ID` in its entry, and its tags let that app through
instead (without it, a repository of the one owner pubkit still knows gets that
owner's app, with a warning): installed on the organization, with contents
write only, and its id and private key as the organization secrets
`RELEASE_APP_ID` and `RELEASE_APP_KEY`, which the CI `pubkit sync` writes
passes to pubrel's release workflow (pubrel 0.6.1 and later), where `pubrel
tag` uses the app's token. So the one way to release is the signed one:
`pubrel prepare` on your machine, then merging the release pull request,
after which CI runs `pubrel tag` on main. Put the tag ruleset on a
repository only once its CI passes the two secrets, or `pubrel tag` is
refused there. A tag pushed by hand is refused. A repository
that releases no tags, such as a site, goes without the tag ruleset:
`"tags": false` in its entry, or `--no-tags`. It calls the GitHub API through
`gh`, signed in as the repositories' owner, since only an admin may change
a ruleset. `--check` changes nothing: it prints how each repository
differs, a setting GitHub adds with its own default aside, and fails when
one does. When GitHub rejects a ruleset, it prints GitHub's reasons.

The repositories come from one of:

- a repos file, with `--repos FILE` ([The repos file](#the-repos-file)): the
  repositories it marks to protect, each with the checks its own `pubkit.json`
  says;
- `--require CHECK`, repeated, and the `REPO` arguments, with no file:
  `pubkit protect --require "ci / passed" example-org/newrepo`.
- the repository's own `pubkit.json`, which says how it is protected:
  `"protect": {"checks": ["ci / passed"], "tags": false, "queue": false, "release_app": 12345}`
  (`tags` and `queue` default to true). A `REPO` is read from the `pubkit.json`
  on its default branch, and `pubkit protect` with no list and no `REPO`,
  run inside a repository, reads `./pubkit.json` for the repository
  `origin` points at. The block takes only `checks`, distinct non-empty
  names, `tags`, `queue` and `auto_merge`, true or false, and `release_app`, a positive integer;
  anything else, or a pubkit.json that is not JSON, is refused before GitHub is asked, and `pubkit
  sync --check` refuses it too.

`pubkit sync` also holds `checks` to what the repository's workflows report
where the ruleset waits for them: on the merge queue's commit (`merge_group`),
or, with `"queue": false`, on the pull request's. A required check nothing
reports there is never passed, so the queue waits for it forever, and sync
fails, naming it. A check counts as reported when a job of a workflow that runs
on that event has it as its name (or id), or, for a check `PREFIX / job`, when
such a job named `PREFIX` calls a reusable workflow (whose own job names sync
does not read). `SonarCloud Code Analysis`, which SonarQube Cloud's app posts,
counts only when such a workflow scans on every run: no workflow sync writes
scans in the merge queue, which SonarQube Cloud refuses, so a repository with
a merge queue drops it from `checks`. `ci / sonar`
still fails `ci / passed` on the quality gate, on the pull request.

It also turns on secret scanning and push protection on each public repository, unless
its block says `"secret_scanning": false`. It never turns them off, and leaves a private
repository alone, since they need GitHub Advanced Security there. Reading them needs
Administration read, and a token without it changes nothing and fails nothing.

`auto_merge` is the repository's "Allow auto-merge" setting, which lets a
pull request be set to merge by itself once the ruleset's required checks
pass (`gh pr merge --auto`). It loosens nothing: the ruleset still decides
what may merge, and without the merge queue the branch must still be up to
date with main. `true` turns it on, `false` off, and without it protect
leaves the setting as it is; `--check` reports a setting that differs.

Here, `make protect` (or `make protect-check`, or `make protect
REPOS=example-org/example-site`) runs it, making the venv first if there is none.

### Pull requests: ready, or draft

A pull request opens **ready for review** by default: the required checks
above gate the merge, and `ci / passed` fails when any CI job does,
SonarQube Cloud's quality gate included (below), so draft adds no safety.
It is **draft** only when something else must happen first -- another
pull request merged, a release published, a pin moved, someone's decision
-- and its description says what it waits on. The managed `AGENTS.md`
carries this rule into every repository, where coding agents read it.

### Branches: up to date with main

Before each push, a branch takes in `origin/main`: rebased onto it if it
was never pushed, `origin/main` merged into it if it was pushed or has a
pull request, so no force push moves a reviewer's view. A conflict is
resolved only when the resolution is clear; otherwise the push waits and
says what conflicts. A folder that only follows main fast-forwards. It
runs when the branch gets new code, never on a timer, so a pull request
arrives as up to date as the ruleset requires. The managed `AGENTS.md`
carries this rule too.

## The release board

The board that grades every repository A to E, and the commands a control
plane runs across many repositories (state files, their GitHub variables,
freshness, and the nightly's loop), are
[pubfleet](https://github.com/Sustenaut/pubfleet)'s. It grades protection
against this pubkit's rulesets, as `pubkit protect --check` does.

## The repos file

Which repositories `pubkit protect` acts on can come from outside it, in one file:

```json
{ "repositories": [
    { "repo": "example-org/some-tool", "protect": true, "govern": true, "project": "tools" },
    { "repo": "example-org/some-site", "protect": true, "govern": false }
] }
```

`protect` says it should carry pubkit's rulesets, and `govern` that a nightly
keeps it current; each is false when left out. `project`, optional, labels it on
the board. How a repository is protected stays in its own `pubkit.json`'s
protect block. `pubkit protect [--check] --repos FILE` reads it; pubfleet reads
the same file, and makes it from an owner's state file.

A repository's `pubkit.json` may name the GitHub variables it reads, never their
values, and the workflow that takes them in:

```json
"variables": { "names": ["SOME_SETTING", "SOME_MAP"], "deploy": "deploy.yml" }
```

`pubkit sync --check` refuses a block of the wrong shape. The values are in the
owner's state file, which pubfleet keeps GitHub to.

## The nightly

`pubkit nightly --pubrel V --body FILE`, run in one repository's checkout,
moves it and its dependencies on to this pubkit, in up to five commits on the
current branch, and writes the pull request's body. pubfleet's `nightly.yml`
runs it in each repository a control plane governs, and keeps one pull
request per repository on its `pubkit-nightly` branch. The commits:

1. every outdated pub* pin (`pubkit pins`): the latest pubkit in any of its
   `*-ci.yml` workflows, pubrel's release workflow and `pubrel-version`,
   each tool `ci.tools` pins by tag, to the latest release of
   its git repository, every other repository's reusable workflow or
   action a hand-written workflow pins at a release
   (`OWNER/REPO/.github/workflows/...@`, `OWNER/REPO/actions/...@`) and
   every other repository's Terraform module a `.tf` file sources at a
   release (`source = "github.com/OWNER/REPO//PATH?ref=..."`), to that
   repository's latest release, except a `deploy.providers` package's,
   which its own bump hook moves, and the crates `nightly.pins.crates`
   names in `Cargo.lock`. A pin moves to the commit of the new release's
   tag, with its version in a comment (for a module source, after the
   closing quote: `?ref=<commit>" # vX.Y.Z`), and one still pinned by tag
   moves to its commit. A pin of a pubkit action, workflow or module this
   pubkit does not ship stays where it is, and `sync --check` fails on it
   until it is taken from the repository it moved to;
2. every third-party GitHub Action (`pubkit actions`) a hand-written
   workflow or one of the repository's own composite actions (`action.yml`,
   outside `node_modules` and `target`) pins by commit with its version in
   a comment, as `uses: actions/checkout@<commit> # v5.1.0`, moved forward
   to its repository's latest release, by the commit of its tag. This
   replaces Dependabot's version updates, whose pull requests can add no
   pubrel row and get no SonarQube Cloud token, so they failed `pubrel
   check` and the sonar job. Major releases come too, gated on CI like
   everything else here. A pin by tag, to a branch (`# main`) or of a
   pre-release stays, as does one whose latest release or tag cannot be
   read, with a warning. The workflows `pubkit sync` writes move with
   pubkit;
3. the other crates' updates (`pubkit crates`) that `cargo vet` covers
   with an audit, adding no exemption, and that `cargo deny` passes. The
   body lists each update skipped with what `cargo vet suggest` says it
   needs;
4. the npm packages, each to the newest version its range in
   `package.json` allows (`npm update --package-lock-only
   --ignore-scripts`);
5. `tests/requirements.lock`, re-resolved from `requirements.txt` (`pubkit
   lock`).

Each commit regenerates `SUPPLY-CHAIN.md` where there is one and records an
`internal` pubrel row for each package it changes. An open pull request is
updated, never a second one opened, by pubfleet's loop.

### Update and security settings

A repository chooses what its nightly does in `pubkit.json`, per language
(`rust`, `node`, `python`; each applies where the repository has that
ecosystem's lock), and for the third-party GitHub Actions (`actions`):

```json
{
  "kind": "utility",
  "update": { "rust": true, "node": true, "python": false },
  "nightly": { "pins": { "crates": ["some-crate"] } },
  "security": {
    "fuzz": { "rust": true },
    "dependabot": { "schedule": "weekly", "ecosystems": ["github-actions"] },
    "sonar": { "args": "-Dsonar.exclusions=vendor/**" }
  }
}
```

| Setting | Default | Off means |
|---|---|---|
| `update.<language>` | `true` | the nightly pull request leaves that language's dependencies alone; `"update": false` turns every language off, and the actions, leaving the pub* pins alone |
| `update.actions` | `true` | the nightly leaves the third-party GitHub Actions alone, and the managed `dependabot.yml` keeps Dependabot's version updates of them |
| `nightly.pins.crates` | three crates, with a warning | the crates the nightly moves with the pins, whatever `cargo vet` says, rather than one at a time; `[]` is none. `pubkit sync --check` refuses any other key in `nightly` or `nightly.pins` |
| `security.fuzz.<language>` | `true` | a scheduled run fuzzes as briefly as any other: `rust-ci.yml` runs cargo-fuzz for `fuzz-seconds` instead of `fuzz-seconds-scheduled`, and `python-ci.yml` and `node-ci.yml` give each property test `fuzz-runs` inputs instead of `fuzz-runs-scheduled` (see [Property tests](#property-tests)) |
| `security.dependabot` | weekly, `github-actions` | `pubkit sync` writes `.github/dependabot.yml` from `schedule` (`daily`, `weekly` or `monthly`), `ecosystems` (names, at the root, or Dependabot's own `{"package-ecosystem", "directory"}`) and `options` (any other Dependabot setting, on every entry). `false` leaves the file to the repository. The languages are the nightly's to update, so the default leaves them out. Every `github-actions` entry ignores the owner's `pubkit/*` and `pubrel/*` workflows and actions, whose pins move with `pubkit bump` alone; ignores in `options` are kept. While the nightly moves the actions (`update.actions`), every `github-actions` entry also sets `open-pull-requests-limit` to 0: Dependabot opens no version updates of them, and its security updates still come |
| `security.sonar` | none | `args` adds SonarQube Cloud properties to every sonar job's scan, after the `sonar-args` input; `organization` is the SonarQube Cloud organization key, by default the GitHub owner in lower case |

A setting pubkit does not know stops the nightly with a message, rather
than being ignored.

Advisory checks have no setting. Every run of `rust-ci.yml`, `node-ci.yml`
and `python-ci.yml`, the nightly included, checks the advisories and fails
on a finding: OSV-Scanner reads every lockfile in the repository against
the OSV database (RustSec, GitHub, PyPI and npm advisories), and each
language adds its own check: cargo deny, `npm audit` of `package-lock.json`,
or pip-audit of the hash-locked requirements. On a public repository's
pull request, GitHub's dependency review also fails a change that brings
in a vulnerable version. Its license check leaves out the owner's own
reusable workflows and actions, for which GitHub reads no license: `pubkit
sync` lists those the repository's workflows call in
`.github/dependency-review-licenses.txt`, and the review takes only the
owner's from it. The pub* pins also always come,
so a governed repository keeps up with the standard. A repository's own CI
runs on a schedule when its `ci.yml` has a `schedule:` trigger; the
settings say what that run does.

### Property tests

Python and Node fuzz with property tests: a test states what holds for
every input, and the library generates inputs to break it, shrinking any
failure to a small example. Python's is [Hypothesis](https://hypothesis.readthedocs.io),
which `tests/requirements-pubkit.txt` pins for every package; Node's is
[fast-check](https://fast-check.dev), which a package adds to its own
`devDependencies`.

Each property tries `PUBKIT_FUZZ_RUNS` inputs, which `python-ci.yml` and
`node-ci.yml` set: `fuzz-runs` (100) on every push and pull request, and
`fuzz-runs-scheduled` (10000) on a scheduled run unless
`security.fuzz.<language>` is `false`. pubkit's pytest plugin reads it for
Hypothesis, with no deadline. fast-check reads no variable of its own, so a
setup file the test command loads (`node --test --import ./tests/setup.mjs`,
or Vitest's `setupFiles`) does:

```js
import fc from "fast-check";
fc.configureGlobal({ numRuns: Number(process.env.PUBKIT_FUZZ_RUNS) || 100 });
```

A failing property prints the input that broke it; Hypothesis also prints a
`@reproduce_failure` blob that replays it exactly.

## Locking

`pubkit lock` resolves with pip's own resolver (`pip install --dry-run
--report`) and needs nothing else. The lock pins the pubkit wheel by URL and
sha256: a release carries the wheel as an asset, and `pip install
--require-hashes` verifies it.

### A wheel mirror, for when GitHub's release downloads fail

A wheel pinned by a GitHub release URL fails to install whenever GitHub's
release downloads do, and pip retries no 504. `tests/install-lock`, which
every managed install of a lock goes through (pubkit.mk's `venv`, the
reusable workflows' `sync --check` steps), tries the install three times,
15 s apart. With a wheel mirror in pubkit.json, a fourth try takes each
wheel the lock pins by a GitHub release URL from the mirror instead:

```json
"mirror": {"wheels": "https://storage.sustenaut.eu/wheels"}
```

The mirror serves each file at `<base>/f/<sha256>/<file>.whl`, so a lock
line alone gives its address, and the hashes stay as they are: pip checks
every file, and the mirror only has to be there, never trusted.
pip-audit's last try reads the locks rewritten the same way
(`tests/install-lock --rewrite LOCK`), and `./pubkit` fetches its own wheel
(by the sha256 tests/requirements.lock pins) and deploy.providers' wheels
from the mirror when GitHub fails to give them. Without the setting, the
installs only retry, and `./pubkit` is as before.

With a PyPI mirror too, the fourth try takes every other package from its
simple repository index (PEP 503) instead of PyPI, again checked against
the lock's hashes:

```json
"mirror": {"wheels": "https://storage.sustenaut.eu/wheels", "pypi": "https://storage.sustenaut.eu/pypi"}
```

Filling the mirror is the mirror's own job. For storage.sustenaut.eu, an
hourly workflow in Sustenaut/storage copies every wheel attached to a
release of Sustenaut's public repositories, and the PyPI files and
Terraform providers the repositories' locks pin.

## CI

```yaml
jobs:
  ci:
    uses: Sustenaut/pubkit/.github/workflows/rust-ci.yml@<commit> # v0.1.0   (or python-ci.yml, node-ci.yml)
    secrets: { SONAR_TOKEN: '${{ secrets.SONAR_TOKEN }}' }          # by name, not inherit
  release:
    needs: ci
    uses: Sustenaut/pubrel/.github/workflows/release.yml@<commit> # v0.1.0
    with: { pubrel-version: v0.1.0 }
    permissions: { contents: write }
```

Pin each one to the full commit its release's tag names, with the version in a comment, as above, rather than to the tag (`@v0.1.0`), which can be moved: SonarQube Cloud flags a tag pin (S7637). `pubkit bump` and the nightly write pubkit's pins this way, and every reader still takes a pin by tag.

- **`rust-ci.yml`:** runs every check the standard requires. A `plan` job builds the hash-locked venv, runs `make sync-check`, and asks `pubkit required` which make targets each job runs. Then:
  - `gates` runs the required gate targets, then any the `gates` input adds;
  - `test` runs `cargo test` on Linux, macOS and Windows (`test-args` adds arguments);
  - `functional` runs the required functional targets, then any the `functional` input adds, optionally with extra apt packages and a released `pub`;
  - `msrv` runs `make msrv MSRV=<the lowest rust-version stated>` on that toolchain;
  - `fuzz` runs `make fuzz-smoke` on nightly (`fuzz-seconds`, and `fuzz-seconds-scheduled` on a schedule unless `pubkit.json` sets `"security": { "fuzz": { "rust": false } }`);
  - `cross` runs `cargo test` under qemu on `cross-targets`: i686 and s390x by default, or aarch64 `-unknown-linux-gnu`, with `cross-test-args`;
  - `audit` runs `make deny` and `make vet`, with `cargo-deny-version` and `cargo-vet-version`;
  - when the caller also runs on a `schedule`, those runs test on the latest stable and beta Rust (`scheduled-toolchains`).

  On a pull request that touches no Rust code (`*.rs`), Cargo files (`Cargo.toml`, `Cargo.lock`, the toolchain pin), `fuzz/` or workflow files (`.github/workflows/`), the plan skips `fuzz` and `cross`, the slow emulated jobs: a documentation or `pubkit.json` change does not wait for them. A release pull request (a `release/` branch), a push to main and the nightly run everything. `pubkit required --event E --head-ref B --changed-files F` decides, and says why on stderr.

  The `gates` and `functional` inputs can only add targets. The `msrv` and `fuzz` inputs are ignored: those jobs run because the standard requires them. Every job reports through `passed`, the one check a ruleset needs. The `sonar` job, in every workflow that has one and in `job-sonar.yml`, waits for SonarQube Cloud's quality gate (`sonar.qualitygate.wait`, for at most 300 seconds), so a failing gate fails the job and `passed` with it, rather than showing only on the SonarCloud app's own check. Without a `SONAR_TOKEN` it still passes with a notice.
- **`python-ci.yml`:** takes its commands as inputs (`setup`, `test`, `browser-test`), so a repository with its own entry point keeps it. A non-empty `browser-test` adds a job that installs Playwright's Chromium. The coverage it writes to `browser-coverage-report` (by default `coverage/`) reaches SonarQube Cloud with the functional job's, and the sonar job waits for it. The `audit` job checks `audit-requirements` (by default `tests/requirements.lock`) with pip-audit on every run.
- **`node-ci.yml`:** for a Node package. It installs from `package-lock.json` with `npm ci --ignore-scripts`, so no dependency runs code by being installed, checks pubkit's managed files from `tests/requirements.lock`, and runs `npm test`. The `audit` job runs `npm audit --package-lock-only` on every run and fails on any advisory, development dependencies included. A non-empty `browser-test` adds a job that installs Playwright's Chromium with `npx --no-install`, which runs only what the lock installed. The coverage it writes to `browser-coverage-report` (by default `coverage/`, LCOV) reaches SonarQube Cloud with the test job's, and the sonar job waits for it. The commands are inputs (`setup`, `test`, `browser-test`, `browser-setup`). A repository with a `ci` block in pubkit.json gets the same jobs generated instead (below).

### Generated CI: only the jobs a repository runs

A shared reusable workflow has to skip the jobs a repository doesn't use, and GitHub lists each one as skipped. A repository of any kind avoids that with a `ci` block in `pubkit.json`. `pubkit sync` then writes one workflow per event, and each workflow calls only the jobs that event runs. Each job is its own reusable workflow (`job-node-test.yml`, `job-rust-gates.yml`, `job-sonar.yml` and so on) at this pubkit's release, so the Actions pins still live in pubkit. `pubkit init` and `pubkit new` write `"ci": {}`.

Sync pins each one, and pubrel's release workflow, to the full commit of the release's tag, with the version in a comment, so SonarQube Cloud's S7637 (a reusable workflow pinned to a tag, which can move) needs no exception:

```yaml
    uses: Sustenaut/pubkit/.github/workflows/job-rust-test.yml@0f3574a72707a46bae149d3ed7a4644b4837795a # v0.15.2
```

Usually neither needs the network. pubkit's commit is in its wheel: the release CI writes the tag's commit into the package (`release-commit`) before it builds the wheel. A source checkout of pubkit has none, and pins by tag. pubrel's commit is the one pubkit records beside the pubrel it pins by default, or the one the repository's workflows already pin at that version; failing both, sync reads it once from pubrel's tags with `git ls-remote`, and when it cannot, pins by tag with a warning. Workflows written before, pinned by tag, still read and check as before.

Whose pubkit and pubrel these are is set in one place, `OWNER` in `src/pubkit/refs.py`: `Sustenaut`, since both moved there from `schryer`. Every repository moves to it on its next `pubkit sync`, and the nightly moves its hand-written `*-ci.yml` and pubrel pins with `pubkit pins` (`pubkit bump` too). A repository can name another owner first with `"workflows_owner": "sustenaut"` in pubkit.json; it also names the repository `git ls-remote` reads pubrel's tags from. `conform`, the nightly and the audit read a pin under any owner, so one still under the old owner keeps working.

```json
"ci": {
  "events": ["pr", "queue", "main", "nightly"],
  "cron": "17 3 * * *",
  "with": {"browser-test": "npm run test:browser", "coverage-report": "coverage/",
           "sonar-args": "-Dsonar.tests=test"},
  "jobs": {"review": false, "sonar": {"events": ["pr", "main"]}},
  "release": {"pubrel": "0.5.4"},
  "tools": [{"crate": "some-cli", "git": "https://github.com/example-org/some-cli", "tag": "v1.2.3"}]
}
```

- **`events`:** which workflows to write. `pr` writes `pr.yml` (pull requests), `queue` writes `queue.yml` (the merge queue's runs of them, `merge_group`), `main` writes `main.yml` (pushes to main), and `nightly` writes `nightly.yml` (`cron`, or by hand). The default is pr, queue and main, and all four for Rust. The queue runs every job a pull request runs, and the heavy ones besides (Rust's `msrv`, `fuzz` and `cross`), so each push to a pull request gets its answer sooner while nothing merges before the heavy jobs pass. Where the queue would run nothing a pull request does not (Node, Python and service repositories, by default), sync writes no `queue.yml` and `pr.yml` runs on `merge_group` too. A repository without a merge queue (a user's, or a private one) leaves `queue` out, so its pull requests run the heavy jobs themselves.
- **`with`:** the jobs' inputs, the same ones `python-ci.yml`, `node-ci.yml`, `rust-ci.yml` and `service-ci.yml` take. Each input goes to every job that takes it, and one that no job takes stops sync.
- **`jobs`:** narrows the events a job runs on, or drops it with `false` (a private repository drops `review`, since GitHub's dependency graph serves only public ones). By default every job runs on every event, except `review`, which runs on pull requests only, and Rust's `msrv`, `fuzz` and `cross`, which run everywhere but on pull requests. `browser` runs only when `with` gives a `browser-test`.
- **`tools`:** Rust's: what the functional suite drives. Each `crate` is installed from its `git` repository at its `tag` with `cargo install --locked`, and the nightly moves the tag to that repository's latest release.
- **`language`:** a utility's, `node` or `python`. Without it, a `package.json` with no `pyproject.toml` beside it means Node. A rust or service repository's CI is its kind's.
- **`release`:** adds pubrel's release workflow to `pr.yml`, `queue.yml` and `main.yml`, after `passed`. It uses this pubkit's pubrel unless `pubrel` names one, and `with` adds inputs (Rust's always checks semver, `semver-checks-version: 0.51.0`). `false` leaves it out. A service deploys rather than releases, so it has none. `{"uses": "local"}` calls the repository's own `.github/workflows/release.yml` in place of pubrel's published one, as pubrel does, so a change to its release workflow is tested before it ships; only `with` goes with it. The nightly moves `release.pubrel` in pubkit.json, then runs `pubkit sync`.

Neither the merge queue nor `main.yml` tests again what an earlier run already tested. A pull request up to date with main merges as the very files its own run tested, and merging moves main to the very commit the queue tested. So `main.yml`, and `pr.yml` where the queue runs it (there is no `queue.yml`), start with `tested` (`job-tested.yml`, `ci / tested`). In the queue and on main, it looks for a successful run of that workflow, on a pull request or in the queue, whose commit has this commit's tree. A run that skipped its own tests names, in a notice, the run it took them from, and that run counts, so long as its artifacts have not expired. When there is one, every job that run ran is skipped (a job only main runs still runs), and `passed` and the release follow. On main, `sonar` still runs, reading the coverage that run uploaded, because SonarQube Cloud measures main from a push to it. The merge queue never scans: SonarQube Cloud analyses pull requests and main, and refuses the queue's `gh-readonly-queue` branch. The pull request's run scanned the change, and main's push scans the merge, so `sonar` does not run on `merge_group` in `pr.yml`, `passed` takes that skip, and a Rust `queue.yml` has no `sonar` at all. So a ruleset with a merge queue must not require SonarQube Cloud's own check, `SonarCloud Code Analysis` (sync refuses it), or the queue waits for that check forever; `ci / sonar` still fails `ci / passed` on a pull request's quality gate. `ci / passed` already takes the skip. When there is none (a pull request's own run, main moved on in between, several pull requests merged together, a repository without the queue, or a commit that reached main some other way) every job runs as it would without `tested`. A Rust crate's `queue.yml` runs jobs its pull requests do not, so its queue tests everything. The nightly always runs everything.

A Rust repository runs the jobs the Rust crate standard asks for here, worked out at sync time from the rules and `pubkit.json`'s exceptions: `gates`, `test`, `functional`, `msrv`, `fuzz`, `cross` and `audit` as required, beside `osv`, `review` and `sonar`, and `toolchains` (the latest stable and beta Rust) on the nightly. Sync writes each job's required make targets into the workflow (`required`), so an exception added or expired shows as a change there, and `make sync-check` in `gates` fails until `pubkit sync` writes it. An empty `cross-targets` (`"[]"`) drops `cross`. `msrv`, `fuzz` and `cross` run in the merge queue rather than on each push to a pull request (unless `events` leaves out `queue`). On a pull request that touches no Rust code, Cargo files, `fuzz/` or workflow files, `fuzz` and `cross` pass at once with a notice (from `pubkit required`, which says `slow=false`) rather than being skipped; release pull requests, pushes and the nightly run them in full.

Uploading a coverage report and restoring or saving a cache only help a run: none of them fails a job. GitHub's artifact and cache services can refuse a run (when the account's Actions billing is blocked they answer with an HTML page, on self-hosted runners too), so every `upload-artifact` of a report and every `actions/cache` step in pubkit's workflows, and every restore and save in `actions/cache`, runs with `continue-on-error`, and the workflow steps stop trying after five minutes. A test that fails still fails its job; a refused upload only leaves the scan without that coverage.

A service runs `checks` (the sync check and shellcheck) and `sonar`, and `terraform` and `tests` when `with` gives a `terraform-dir` and a `test`. Its tests hand `sonar` the coverage they write to `coverage-report` (by default `coverage/`, Cobertura XML or LCOV `lcov.info`). A private service drops the scan with `"jobs": {"sonar": false}`.

A repository that deploys a static site with pubhost's `deploy-static` also gets a `site` job in `pr.yml` (`ci / site`, part of `ci / passed`), so a site that would fail to deploy fails the pull request instead of the deploy after merge. It checks the repository out, builds the site, and runs the `deploy-static` the repository's own deploy workflow pins, as it pins it, with `check-only: 'true'`, which runs its site check and deploys nothing, so it needs no secret. The job comes when any workflow of the repository's own uses `deploy-static`, or pubkit.json has a `site` block, which then needs such a workflow to take the pin from:

```json
"site": {"check_dir": "public", "pr_build": "npm run build:sample", "build": "npm run build",
         "url": "https://example.eu/"}
```

- **`check_dir`:** the directory to check, inside the repository. Without it, the `dir` the deploy workflow gives `deploy-static`.
- **`pr_build`:** the command that builds the site for the check, such as a build from a small sample of its data where the full build is slow. Without it, `build`; without either, the directory is checked as it is in the repository.
- **`url`:** the site's https:// address, so the check finds its og:image files.

Each is one line of text, and sync refuses any other key.

The checks keep the names a ruleset requires: each job is named `ci`, so its checks read `ci / test`, `ci / sonar` and so on. Every workflow ends in `ci / passed`, which passes only when every job succeeded. The generated workflows are managed files, so `pubkit sync --check` fails when one is edited, and also while a hand-written `ci.yml` is still beside them. A repository without a ci block keeps its own `ci.yml`, which can still call `rust-ci.yml`, `python-ci.yml`, `node-ci.yml` or `service-ci.yml`.

### SonarQube Cloud

Public code integrates with SonarQube Cloud, whatever its kind: `rust-ci.yml`, `python-ci.yml`, `node-ci.yml` and `service-ci.yml` each run a `sonar` job, part of `passed`, and so does the CI `pubkit sync` writes for every kind. Only a private repository, which SonarQube Cloud does not analyse for free, leaves it out (`"jobs": {"sonar": false}`). Given a `SONAR_TOKEN` secret, passed by name (`secrets: { SONAR_TOKEN: '${{ secrets.SONAR_TOKEN }}' }`, since `secrets: inherit` reaches only a workflow of the caller's own owner and GitHub drops it without a word for pubkit's once the owners differ), it sends the code to SonarQube Cloud as the project `OWNER_REPO` (the owner as GitHub spells it) in the organization whose key is the owner in lower case, or `security.sonar.organization` in pubkit.json, with the full history so it can tell new code from old; `sonar-args` adds properties. Without a token, as in a private repository or a pull request from a fork, it passes with a notice. To turn it on for a public repository:

1. In SonarQube Cloud, analyse the repository as a new project in the organization whose key is its GitHub owner in lower case (or the one `security.sonar.organization` names), keeping the key `OWNER_REPO`.
2. Under the project's Administration, Analysis Method, turn Automatic Analysis off: the two cannot run together.
3. Add the token as the repository's `SONAR_TOKEN` Actions secret.

The default quality gate asks for 80% coverage on new code, and a scan without a coverage report counts it as 0%, so each workflow hands the sonar job one:

| Workflow | Report | Written by |
|---|---|---|
| `rust-ci.yml` | `coverage/lcov.info` | `make coverage`, which the functional job runs in place of `make functional` when there is a token: the Rust tests and the Gherkin suite on a build cargo-llvm-cov instruments (`cargo-llvm-cov-version`) |
| `rust-ci.yml` | `coverage/*.xml` (Cobertura) | any of the `functional` targets, for a repository's Python, such as coverage.py's `coverage xml -o coverage/scripts.xml` (run it after `make coverage`, which empties `coverage/`) |
| `python-ci.yml` | `coverage-report`, by default `coverage/coverage.xml` (Cobertura) | the `test` command, such as coverage.py with `patch = ["subprocess"]`, which also measures the programs the suite starts (pubkit's own `make functional` does this) |
| `node-ci.yml` | `coverage-report`, by default `coverage/lcov.info` | the `test` command, such as `node --test --experimental-test-coverage --test-reporter=lcov --test-reporter-destination=coverage/lcov.info` |
| `service-ci.yml` | `coverage-report`, by default `coverage/`: Cobertura `*.xml` or LCOV `lcov.info` | the `test` command, as for Python or Node |

A test command that writes no report leaves the scan as it was.

In the CI `pubkit sync` writes, the scan also learns which code is tests, so SonarQube Cloud does not warn that files "look like test code" while `sonar.tests` is unset. Sync gives the sonar job a `layout` from the directories the repository has:

| Language | `sonar.sources` | `sonar.tests` |
|---|---|---|
| Python | `src` | `tests` |
| Rust | `src`, `crates` (a workspace) | `tests`, `fuzz`, `benches` |

Each lists only the directories that exist, since the scan fails on one that does not. Without any test directory, and for Node and services, sync gives no layout and SonarQube Cloud keeps its defaults. With tests but no code directory, the code is the whole repository less its tests (`sonar.exclusions`), so no file is read as both. `sonar-args` and `security.sonar.args` come after the layout, and the scanner takes the last value a property is given, so a repository's own `sonar.sources` or `sonar.tests` wins. Set both if you set one, or a file can end up in both.

Every action these workflows use, pubrel's release workflow included, is pinned to a commit, with its version in a comment, and a test fails on one that is not.

## Developing

```sh
make venv     # hash-locked environment, this checkout installed editable
make check    # standard.json current, then the Gherkin suite
```

The suite drives the plugin through pytest's `pytester`. Each scenario
builds a throwaway suite, runs it in a subprocess, and judges what pytest
reports. It drives the command in a scratch repository. pubkit is its own
first consumer: its CI runs through `python-ci.yml` from this checkout, and
it is released with [pubrel](https://github.com/Sustenaut/pubrel). Its
package publet, `pkg.pubkit`, lives in `corpus/`.

## Licence

Apache-2.0: see [`LICENSE`](LICENSE).
