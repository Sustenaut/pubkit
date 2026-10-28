Feature: pubkit audit prepare writes the audit with every machine-decidable rule decided
  `pubkit audit prepare --package NAME` writes audit/<package>-<version>.md
  for the release `pubrel next` gives: the header `pubkit audit --check`
  reads, each finding a machine can make under its severity with its file
  and line, and each rule that needs judgement marked [needs review], so
  the report cannot pass the check until a person has judged them.

  Scenario: The report's header names the release, the standard, and the commit audited
    Given the crate "library" released as "v0.1.0" and changed since, with pubrel giving "0.1.1"
    When I run pubkit "audit" "prepare" "--package" "demo" "--quick"
    Then it succeeds
    And it prints "wrote audit/demo-0.1.1.md"
    And the report "demo" "0.1.1" has the header "package: demo"
    And the report "demo" "0.1.1" has the header "version: 0.1.1"
    And the report "demo" "0.1.1" has the header "last-release: v0.1.0"
    And the report "demo" "0.1.1" names the current standard and the commit audited

  Scenario Outline: Each machine-decidable finding is in its section, with its file and line
    Given the crate "library" released as "v0.1.0" and changed since, with pubrel giving "0.1.1"
    When I run pubkit "audit" "prepare" "--package" "demo" "--quick"
    Then it succeeds
    And the report "demo" "0.1.1" lists under "<section>" "<finding>"

    Examples:
      | section                     | finding                                                                    |
      | Blocks a release            | (`src/lib.rs:47`). `thrice` has no example                                 |
      | Blocks a release            | release.json does not set `"semver-checks": true` for this package         |
      | Falls short of the standard | (`src/lib.rs:58`). `parse` returns a `Result` and its docs have no `# Errors` |
      | Falls short of the standard | (`src/lib.rs:55`). `parse`'s example unwraps                               |
      | Falls short of the standard | `ParseError` is a public error enum without `#[non_exhaustive]`            |
      | Falls short of the standard | the crate docs have no Why this crate? section                             |
      | Falls short of the standard | (`src/lib.rs:26`). the row for `lexical` gives no version checked          |
      | Falls short of the standard | the include pattern "README.md" is not anchored with `/`                   |
      | Falls short of the standard | `documentation` is not set                                                 |
      | Falls short of the standard | it names OSV-Scanner, which no workflow in .github/workflows runs           |
      | Falls short of the standard | the README has no Minimum Rust version section                             |
      | Worth considering           | (`src/lib.rs:25`). the row for `itoa` says 1.0.9; Cargo.lock has 1.0.15      |
      | Worth considering           | run cargo vet, which SECURITY.md does not mention                          |

  Scenario: The rules that need judgement are left for review, so the report cannot pass yet
    Given the crate "library" released as "v0.1.0" and changed since, with pubrel giving "0.1.1"
    And the audit of "demo" has been prepared
    When I run pubkit "audit" "--check" "demo" "0.1.1"
    Then it fails
    And the report "demo" "0.1.1" marks "§2.2 Hidden setup lines" for review, having found "`parse`'s example hides 1 setup line(s)"
    And the report "demo" "0.1.1" marks "§1.6 Related crates" for review, having found "`itoa` 1.0.9; Cargo.lock: 1.0.15"

  Scenario: Once a person has judged every rule and nothing blocks, the report passes
    Given the crate "library" released as "v0.1.0" and changed since, with pubrel giving "0.1.1"
    And the audit of "demo" has been prepared
    When a person reviews the report "demo" "0.1.1" and resolves what blocks
    And I run pubkit "audit" "--check" "demo" "0.1.1"
    Then it succeeds

  Scenario: A command-line tool is held to section 9, and its --help is run
    Given the crate "tool" released as "v0.1.0" and changed since, with pubrel giving "0.1.1"
    When I run pubkit "audit" "prepare" "--package" "demo-tool" "--quick"
    Then it succeeds
    And the report "demo-tool" "0.1.1" lists under "Falls short of the standard" "`demo-tool --help` exits 2, with nothing on stdout and output on stderr"
    And the report "demo-tool" "0.1.1" lists under "Falls short of the standard" "for a command-line tool it points at the README"
    And the report "demo-tool" "0.1.1" lists under "Falls short of the standard" "the README has no why this tool section"
    And "audit/demo-tool-0.1.1.md" contains "**Not applicable to a command-line tool (binary only):** §1.1"

  Scenario: A report that may hold a person's review is not written over
    Given the crate "library" released as "v0.1.0" and changed since, with pubrel giving "0.1.1"
    And the audit of "demo" has been prepared
    When I run pubkit "audit" "prepare" "--package" "demo" "--quick"
    Then it fails
    And stderr mentions "audit/demo-0.1.1.md exists"

  Scenario: The version can be given, and the report written elsewhere
    Given the crate "library" released as "v0.1.0" and changed since, with pubrel giving "0.1.1"
    When I run pubkit "audit" "prepare" "--package" "demo" "--version" "0.2.0" "--out" "elsewhere.md" "--quick"
    Then it succeeds
    And "elsewhere.md" contains "version: 0.2.0"

  Scenario: Without pubrel, the version must be given
    Given the crate "library" released as "v0.1.0" and changed since, with no pubrel
    When I run pubkit "audit" "prepare" "--package" "demo" "--quick"
    Then it fails
    And stderr mentions "install it, or give --version"

  Scenario: A package that is not here is named
    Given the crate "library" released as "v0.1.0" and changed since, with pubrel giving "0.1.1"
    When I run pubkit "audit" "prepare" "--package" "other" "--quick"
    Then it fails
    And stderr mentions "no package here named other"

  Scenario: Without --quick, the slower checks run or say why they could not
    Given the crate "library" released as "v0.1.0" and changed since, with pubrel giving "0.1.1"
    When I run pubkit "audit" "prepare" "--package" "demo"
    Then it succeeds
    And the report "demo" "0.1.1" lists under "Checks run" "`cargo publish --dry-run --locked -p demo`"
    And the report "demo" "0.1.1" lists under "Checks not run" "**make targets:** no Makefile"
    And the report "demo" "0.1.1" accounts for the docs.rs build and the MSRV build
