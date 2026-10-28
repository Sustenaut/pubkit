Feature: A release cites a current quality audit
  The rust-crate-quality agent writes its report on a release to
  audit/<package>-<version>.md, with a header naming the standard it audited
  against and how many findings block a release. `pubkit audit --check`
  passes only on a report for that release, against the standard pubkit
  ships, with none that block. Declared as a security check in release.json,
  it stops `pubrel prepare` from cutting a release without one.

  Background:
    Given an empty repository

  Scenario: A current report with nothing blocking passes
    Given an audit report on "demo" "0.2.0" against the current standard with 0 blocking
    When I run pubkit "audit" "--check" "demo" "0.2.0"
    Then it succeeds
    And it prints "audit/demo-0.2.0.md: demo 0.2.0 audited on 2026-10-13 by rust-crate-quality against the current standard"

  Scenario: As a security check, it reads the release pubrel is cutting
    Given an audit report on "demo" "0.2.0" against the current standard with 0 blocking
    And pubrel is cutting "demo" "0.2.0"
    When I run pubkit "audit" "--check"
    Then it succeeds

  Scenario Outline: A report that cannot let the release through fails
    Given <report>
    When I run pubkit "audit" "--check" "demo" "0.2.0"
    Then it fails
    And stderr mentions "<why>"

    Examples:
      | report                                                                              | why                                           |
      | an audit report on "demo" "0.1.0" against the current standard with 0 blocking      | no audit/demo-0.2.0.md                        |
      | an audit report on "demo" "0.2.0" against the current standard with 2 blocking      | lists 2 finding(s) that block a release       |
      | an audit report on "demo" "0.2.0" against an older standard with 0 blocking          | audited against another version of the standard |
      | an audit report on "demo" "0.2.0" with no header                                     | has no header                                 |

  Scenario: A missing report names the command that starts one
    When I run pubkit "audit" "--check" "demo" "0.2.0"
    Then it fails
    And stderr mentions "run `pubkit audit prepare --package demo`"

  Scenario Outline: A rule nobody has judged keeps the release back
    Given an audit report on "demo" "0.2.0" against the current standard with 0 blocking, and a rule marked "<mark>"
    When I run pubkit "audit" "--check" "demo" "0.2.0"
    Then it fails
    And stderr mentions "1 rule(s) nobody has judged yet (marked <mark>)"

    Examples:
      | mark                                                   |
      | [needs review]                                         |
      | [machine-drafted by claude-opus-5-5: needs human review] |

  Scenario: The standard's digest is printed for a report's header
    When I run pubkit "audit" "--standard"
    Then it succeeds
    And it prints the sha256 of the standard pubkit ships
