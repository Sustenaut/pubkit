Feature: The required checks come from the standard
  Every `[checked: ...]` rule in the Rust crate standard has a stable ID and
  names what checks it. `standard.json`, generated from those tags, ships in
  pubkit. `pubkit required` reads it to list the make targets each
  rust-ci.yml job must run in a repository, so no repository can leave one
  out.

  Scenario: standard.json is current with the standard's tags
    When standard.json is generated from the standard
    Then it is the committed standard.json

  Scenario: Every checked rule in the standard has an ID
    When I read the standard
    Then every "[checked" tag names a rule ID
    And no rule ID tags two rules

  Scenario: pubkit.mk provides every make target the standard names
    When I read the standard.json pubkit ships
    Then the rust pubkit.mk defines every target it names

  Scenario: A library is held to every rule
    Given a Rust repository whose crate "demo" has a library
    When I run pubkit "required"
    Then it succeeds
    And it prints "gates=fmt-check lint doc doc-coverage-check publish-check"
    And it prints "functional=functional vectors-check supply-chain-check"
    And it prints "msrv=msrv"
    And it prints "fuzz=fuzz-smoke"
    And it prints "audit=deny vet"
    And it prints "cross_job=true"

  Scenario: A command-line tool is not held to the library rules
    Given a Rust repository whose crate "demo" is only a binary
    When I run pubkit "required"
    Then it succeeds
    And it prints "gates=fmt-check lint doc publish-check"

  Scenario: The targets are handed to GitHub Actions
    Given a Rust repository whose crate "demo" has a library
    And GITHUB_OUTPUT names a file
    When I run pubkit "required" "--github-output"
    Then it succeeds
    And the GITHUB_OUTPUT file says "audit=deny vet"

  Scenario: A Python repository has no Rust standard to meet
    Given a repository initialised as "utility"
    When I run pubkit "required"
    Then it fails
    And stderr mentions "not a Rust repository"

  Scenario Outline: A pull request that touches no code skips fuzz and cross
    Given a Rust repository whose crate "demo" has a library
    And the pull request changes "<paths>"
    When I run pubkit "required" "--event" "<event>" "--head-ref" "<branch>" "--changed-files" "changed"
    Then it succeeds
    And the plan line for "fuzz" is "<fuzz>"
    And it prints "cross_job=<cross>"
    And it prints "slow=<cross>"

    Examples:
      | event             | branch          | paths                          | fuzz       | cross |
      | pull_request      | claude/docs     | README.md,.claude/notes.md     | (none)     | false |
      | pull_request      | claude/docs     | README.md,src/lib.rs           | fuzz-smoke | true  |
      | pull_request      | claude/deps     | Cargo.lock                     | fuzz-smoke | true  |
      | pull_request      | claude/fuzz     | fuzz/regressions/parse/x       | fuzz-smoke | true  |
      | pull_request      | claude/ci       | .github/workflows/ci.yml       | fuzz-smoke | true  |
      | pull_request      | claude/tool     | rust-toolchain.toml            | fuzz-smoke | true  |
      | pull_request      | release/v0.2.0  | CHANGELOG.md                   | fuzz-smoke | true  |
      | push              |                 | README.md                      | fuzz-smoke | true  |
      | schedule          |                 | README.md                      | fuzz-smoke | true  |

  Scenario: The changed files are read only from the repository or a temporary directory
    Given a Rust repository whose crate "demo" has a library
    When I run pubkit "required" "--event" "pull_request" "--head-ref" "claude/docs" "--changed-files" "/etc/pubkit-changed"
    Then it fails
    And stderr mentions "must be in the working directory or a temporary directory"

  Scenario: Without the changed files, nothing is skipped
    Given a Rust repository whose crate "demo" has a library
    When I run pubkit "required" "--event" "pull_request" "--head-ref" "claude/docs"
    Then it succeeds
    And it prints "fuzz=fuzz-smoke"
    And it prints "cross_job=true"
