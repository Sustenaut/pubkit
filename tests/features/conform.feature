Feature: A Rust repository conforms to the standard, or says why not
  `pubkit conform` checks what no make target can: the files and settings
  the Rust crate standard requires, and that the workflow pins the pubkit
  the lock pins. `pubkit sync --check` runs it, so CI does on every change.
  A rule the repository cannot meet yet is declared as an exception in
  pubkit.json, with a reason and a date. It is skipped until that date, and
  fails after it.

  Scenario: A conforming crate passes
    Given a Rust crate that meets the standard
    When I run pubkit "conform"
    Then it succeeds
    And it prints "conforms to the Rust crate standard"

  Scenario: sync --check runs the conformance checks
    Given a Rust crate that meets the standard
    And the file "SECURITY.md" is removed
    When I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "RS-SUP-04: no SECURITY.md"

  Scenario Outline: A missing file or setting fails its rule
    Given a Rust crate that meets the standard
    And <change>
    When I run pubkit "conform"
    Then it fails
    And stderr mentions "<message>"

    Examples:
      | change                                                   | message                                          |
      | the file "SECURITY.md" is removed                        | RS-SUP-04: no SECURITY.md                        |
      | the directory "testdata" is removed                      | RS-TEST-01: no testdata/                         |
      | the directory "fuzz" is removed                          | RS-FUZZ-01: no fuzz/fuzz_targets/*.rs            |
      | unsafe code is allowed                                   | RS-SAFE-01: Cargo.toml: set unsafe_code          |
      | missing docs are allowed                                 | RS-DOC-03: Cargo.toml: set missing_docs          |
      | the library has no _documentation module                 | RS-DOC-01: src/lib.rs: declare `pub mod _documentation` |
      | deny.toml allows yanked crates                           | RS-SUP-01: deny.toml: set yanked = "deny"        |
      | the supply chain imports no audits from mozilla          | RS-SUP-01: supply-chain/config.toml: import mozilla |

  Scenario: A workflow pinning another pubkit fails
    Given a Rust crate that meets the standard
    And the workflow calls rust-ci.yml at "v0.0.1"
    When I run pubkit "conform"
    Then it fails
    And stderr mentions "rust-ci.yml@v0.0.1 is not pubkit"

  Scenario: An exception skips its rule, and is listed
    Given a Rust crate that meets the standard
    And the directory "testdata" is removed
    And an exception for "RS-TEST-01" until "2099-01-01" because "no independent source yet"
    When I run pubkit "conform"
    Then it succeeds
    And it prints "open exception: RS-TEST-01 until 2099-01-01: no independent source yet"

  Scenario: An exception past its date fails
    Given a Rust crate that meets the standard
    And the directory "testdata" is removed
    And an exception for "RS-TEST-01" until "2026-01-01" because "no independent source yet"
    When I run pubkit "conform"
    Then it fails
    And stderr mentions "RS-TEST-01: the exception expired on 2026-01-01"

  Scenario: An exception must name a rule of the standard
    Given a Rust crate that meets the standard
    And an exception for "RS-NOPE-01" until "2099-01-01" because "typo"
    When I run pubkit "conform"
    Then it fails
    And stderr mentions "RS-NOPE-01 is not a rule of the standard"

  Scenario: An excepted rule's targets leave the plan
    Given a Rust crate that meets the standard
    And an exception for "RS-TEST-01" until "2099-01-01" because "no independent source yet"
    When I run pubkit "required"
    Then it succeeds
    And it prints "functional=functional supply-chain-check"
