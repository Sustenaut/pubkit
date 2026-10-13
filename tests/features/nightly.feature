Feature: The nightly keeps every pub* repository current, through a pull request
  `pubkit pins` moves every outdated pub* pin, `pubkit crates` keeps only
  the crate updates cargo vet audits and cargo deny passes, and `pubkit
  nightly` commits each, writing the pull request's body. The nightly
  workflow keeps one pull request per repository and never merges it.

  Background:
    Given an empty repository

  Scenario: pins moves every workflow pin to the latest releases
    Given the file ".github/workflows/ci.yml" reads "uses: schryer/pubrel/.github/workflows/release.yml@v0.5.1 with: { pubrel-version: v0.5.1, pub-version: v0.2.0 }"
    When I run pubkit "pins" "--pubrel" "0.5.3" "--pub" "0.2.1" "--no-lock"
    Then it succeeds
    And it prints "ci.yml: pubrel release workflow 0.5.1 -> 0.5.3"
    And it prints "ci.yml: pub-version 0.2.0 -> 0.2.1"
    And ".github/workflows/ci.yml" contains "release.yml@v0.5.3 with: { pubrel-version: v0.5.3, pub-version: v0.2.1 }"

  Scenario: pins on a repository already current changes nothing
    Given the file ".github/workflows/ci.yml" reads "uses: schryer/pubrel/.github/workflows/release.yml@v0.5.3"
    When I run pubkit "pins" "--pubrel" "0.5.3" "--pub" "0.2.1" "--no-lock"
    Then it succeeds
    And it prints nothing

  Scenario: The crates cargo vet finds unaudited are the ones skipped
    When cargo vet reports "  zeroize:1.9.1 missing [\"safe-to-deploy\"]" and "  syn:3.0.9 missing [\"safe-to-run\"]"
    Then the crates skipped are "syn, zeroize"

  Scenario: The body lists every skipped crate with what cargo vet suggest says
    When the nightly writes a body with "zeroize" skipped because "cargo vet diff zeroize 1.8.2 1.9.1"
    Then the body says nothing merges it
    And the body shows "zeroize 1.9.0 -> 1.9.1" with "cargo vet diff zeroize 1.8.2 1.9.1"

  Scenario: The nightly workflow updates one pull request and merges nothing
    When I parse the workflow "nightly.yml"
    Then it runs on a schedule and by hand
    And neither it nor its script merges a pull request
    And its script updates an open pull request before opening one
    And its script re-tests the pins alone when CI fails

  Scenario: pubkit new writes a package that conforms but for its release key
    When I start the package "pubfoo" without a key
    Then the repository has "Cargo.toml", "src/lib.rs", "release.json", "SECURITY.md", "TESTING.md", "SUPPLY-CHAIN.md", "fuzz/fuzz_targets/greet.rs", ".gitignore"
    And it conforms to the standard but for "RS-SUP-05"
