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

  Scenario: A release key labelled with a person's name fails
    Given a Rust crate that meets the standard
    And the release key is labelled "Jane Doe"
    When I run pubkit "conform"
    Then it fails
    And stderr mentions "RS-SUP-05: the release key is labelled 'Jane Doe'"
    And stderr mentions "such as 'demo release key'"

  Scenario: The release key's object must be exported to be checked
    Given a Rust crate that meets the standard
    And the release key's object is not in corpus/objects
    When I run pubkit "conform"
    Then it fails
    And stderr mentions "RS-SUP-05: the key object pub:sha2-256:demokey is not in corpus/objects/"

  Scenario: A release must be gated on a current quality audit
    Given a Rust crate that meets the standard
    And release.json declares no audit check
    When I run pubkit "conform"
    Then it fails
    And stderr mentions "RS-REL-01: release.json declares no `pubkit audit --check` security check"

  Scenario: A workflow pinning another pubkit fails
    Given a Rust crate that meets the standard
    And the workflow calls rust-ci.yml at "v0.0.1"
    When I run pubkit "conform"
    Then it fails
    And stderr mentions "rust-ci.yml@v0.0.1 is not pubkit"

  Scenario: A workflow pinning this pubkit's tag's commit conforms
    Given a Rust crate that meets the standard
    And pubkit is a release whose tag names "0f3574a72707a46bae149d3ed7a4644b4837795a"
    And the workflow calls rust-ci.yml at commit "0f3574a72707a46bae149d3ed7a4644b4837795a" of "this pubkit"
    When I run pubkit "conform"
    Then it succeeds

  Scenario: A workflow pinning another pubkit's commit fails
    Given a Rust crate that meets the standard
    And the workflow calls rust-ci.yml at commit "1a49eb98c9647c1b60073d55b9ca0daf3eeee5c0" of "0.0.1"
    When I run pubkit "conform"
    Then it fails
    And stderr mentions "rust-ci.yml@v0.0.1 is not pubkit"

  Scenario: A workflow pinning a commit other than this pubkit's tag's fails
    Given a Rust crate that meets the standard
    And pubkit is a release whose tag names "0f3574a72707a46bae149d3ed7a4644b4837795a"
    And the workflow calls rust-ci.yml at commit "1a49eb98c9647c1b60073d55b9ca0daf3eeee5c0" of "this pubkit"
    When I run pubkit "conform"
    Then it fails
    And stderr mentions "rust-ci.yml@1a49eb98c9647c1b60073d55b9ca0daf3eeee5c0 is not the commit of pubkit"

  Scenario: A workflow calling pubkit under another owner still conforms
    Given a Rust crate that meets the standard
    And the workflow calls "sustenaut"'s rust-ci.yml at "this pubkit"
    When I run pubkit "conform"
    Then it succeeds

  Scenario: Without a workflow calling rust-ci.yml, conform names workflows_owner's
    Given a Rust crate that meets the standard
    And pubkit.json names the workflows owner "sustenaut"
    And the file ".github/workflows/ci.yml" reads "jobs: {}"
    When I run pubkit "conform"
    Then it fails
    And stderr mentions "no workflow calls sustenaut/pubkit/.github/workflows/rust-ci.yml"

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
