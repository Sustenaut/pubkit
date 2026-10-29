Feature: Keeping a repository's shared files in step
  `pubkit init` writes the shared files; `pubkit sync --check` fails when a
  managed one differs from the installed pubkit's, so copies cannot drift.

  Scenario: init writes managed and seeded files for a Rust package
    Given an empty repository
    When I run pubkit "init" "--kind=rust" "--bin=demo"
    Then it succeeds
    And the repository has "AGENTS.md", "pubkit.mk", "rust-toolchain.toml", "tests/requirements-pubkit.txt", "tests/requirements.txt", "tests/pytest.ini", "Makefile", ".github/workflows/pr.yml", ".github/workflows/main.yml", ".github/workflows/nightly.yml"
    And "Makefile" contains "DEMO_BIN_DIR"
    And "tests/requirements-pubkit.txt" pins this pubkit's wheel

  Scenario: every repository gets the managed AGENTS.md, with the draft rule
    Given an empty repository
    When I run pubkit "init" "--kind=utility"
    Then it succeeds
    And it prints "managed  AGENTS.md"
    And "AGENTS.md" contains "Mark it **draft** only when something else must happen first"
    And "AGENTS.md" contains "Managed by pubkit"

  Scenario: An edited AGENTS.md fails the check, and sync restores it
    Given a repository initialised as "rust"
    And the file "AGENTS.md" reads "# Our own rules"
    When I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "AGENTS.md differs"
    When I run pubkit "sync"
    Then it succeeds
    And it prints "updated  AGENTS.md"

  Scenario: init keeps the repository's own files
    Given an empty repository
    And the file "tests/pytest.ini" reads "[pytest]"
    When I run pubkit "init" "--kind=utility"
    Then it succeeds
    And it prints "kept     tests/pytest.ini"
    And "tests/pytest.ini" contains "[pytest]"

  Scenario: init gives a Node package the Node CI, generated from pubkit.json
    Given an empty repository
    And the file "package.json" reads "{}"
    When I run pubkit "init" "--kind=utility"
    Then it succeeds
    And "pubkit.json" contains "ci"
    And ".github/workflows/pr.yml" contains "job-node-test.yml"
    And ".github/workflows/main.yml" contains "secrets: { SONAR_TOKEN: '${{ secrets.SONAR_TOKEN }}' }"

  Scenario: init gives a package with a pyproject.toml the Python CI, even beside a package.json
    Given an empty repository
    And the file "package.json" reads "{}"
    And the file "pyproject.toml" reads "[project]"
    When I run pubkit "init" "--kind=utility"
    Then it succeeds
    And ".github/workflows/pr.yml" contains "job-python-functional.yml"

  Scenario: A freshly initialised repository is in step
    Given a repository initialised as "utility"
    When I run pubkit "sync" "--check"
    Then it succeeds
    And it prints "in step with pubkit"

  Scenario: An edited managed file fails the check, and sync restores it
    Given a repository initialised as "rust"
    And the file "pubkit.mk" reads "edited"
    When I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "pubkit.mk differs"
    When I run pubkit "sync"
    Then it succeeds
    And it prints "updated  pubkit.mk"
    When I run pubkit "sync" "--check"
    Then it succeeds

  Scenario: Seeded files are the repository's to change
    Given a repository initialised as "rust"
    And the file "Makefile" reads "include pubkit.mk"
    When I run pubkit "sync" "--check"
    Then it succeeds

  Scenario: A lock pinning another pubkit fails the check
    Given a repository initialised as "utility"
    And the file "tests/requirements.lock" reads "pubkit @ https://example.org/download/v0.0.0/pubkit-0.0.0-py3-none-any.whl"
    When I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "does not pin pubkit"

  Scenario: A pubkit other than the pinned release refuses to judge by the pin
    Given a repository initialised as "utility"
    And the repository pins pubkit "0.0.1"
    When I run pubkit "sync" "--check"
    Then the exit code is 2
    And stderr mentions "this repository pins pubkit 0.0.1 (tests/requirements-pubkit.txt)"
    And stderr mentions "Run `make venv`"
    When I run pubkit "audit" "--standard"
    Then the exit code is 2

  Scenario: The release check can be skipped, and moving to another release is never refused
    Given a repository initialised as "utility"
    And the repository pins pubkit "0.0.1"
    And PUBKIT_IGNORE_VERSION is set
    When I run pubkit "audit" "--standard"
    Then it succeeds
    When I run pubkit "sync"
    Then it succeeds

  Scenario: A Rust package's .venv is rebuilt whenever its lock changes
    Given a repository initialised as "rust"
    Then pubkit.mk rebuilds .venv when tests/requirements.lock changes, before running .venv/bin/pubkit

  Scenario: sync outside an initialised repository says what to do
    Given an empty repository
    When I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "run `pubkit init"

  Scenario: lock pins every file the index publishes, so it installs on any platform
    Given an empty repository
    And a package "demo" 1.0 whose index lists files hashed "aaaa" and "bbbb"
    And the file "tests/requirements.txt" reads "demo==1.0"
    When I run pubkit "lock"
    Then it succeeds
    And "tests/requirements.lock" pins "demo==1.0" with the hashes "aaaa", "bbbb" and the resolved file's

  Scenario: The built wheel carries every template, and the standard's rules
    When pubkit's wheel is built
    Then it holds every file under src/pubkit/templates, and standard.json

  Scenario: supply-chain writes each crate's report beside its manifest
    Given a crate "demo" with no dependencies, vetted with audits imported from "mozilla" and "zcash"
    When I run pubkit "supply-chain" "demo"
    Then it succeeds
    And it prints "wrote SUPPLY-CHAIN.md"
    And "SUPPLY-CHAIN.md" contains "Generated by `pubkit supply-chain` from `supply-chain/`; do not edit."
    And "SUPPLY-CHAIN.md" contains "0 dependencies"
    And "SUPPLY-CHAIN.md" contains "Audits are imported from Mozilla and Zcash."

  Scenario: supply-chain --check passes on a current report and fails on a stale one
    Given a crate "demo" with no dependencies, vetted with audits imported from "mozilla" and "zcash"
    And pubkit "supply-chain" "demo" has run
    When I run pubkit "supply-chain" "--check" "demo"
    Then it succeeds
    And it prints "SUPPLY-CHAIN.md current for demo"
    When the file "SUPPLY-CHAIN.md" reads "edited"
    And I run pubkit "supply-chain" "--check" "demo"
    Then it fails
    And stderr mentions "stale; run `make supply-chain`: SUPPLY-CHAIN.md"

  Scenario: supply-chain names a crate that is not here
    Given a crate "demo" with no dependencies, vetted with audits imported from "mozilla" and "zcash"
    When I run pubkit "supply-chain" "other"
    Then it fails
    And stderr mentions "no crate here named other"

  Scenario: supply-chain needs cargo vet set up
    Given an empty repository
    When I run pubkit "supply-chain" "demo"
    Then it fails
    And stderr mentions "no supply-chain/config.toml here; run `cargo vet init` first"
