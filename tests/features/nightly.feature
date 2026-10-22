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

  Scenario: pins moves pubkit's pin in every one of its reusable workflows
    Given the file ".github/workflows/ci.yml" reads "uses: schryer/pubkit/.github/workflows/node-ci.yml@v0.0.1"
    When I run pubkit "pins" "--pubrel" "0.5.3" "--pub" "0.2.1" "--no-lock"
    Then it succeeds
    And ".github/workflows/ci.yml" does not contain "node-ci.yml@v0.0.1"

  Scenario: pins on a repository already current changes nothing
    Given the file ".github/workflows/ci.yml" reads "uses: schryer/pubrel/.github/workflows/release.yml@v0.5.3"
    When I run pubkit "pins" "--pubrel" "0.5.3" "--pub" "0.2.1" "--no-lock"
    Then it succeeds
    And it prints nothing

  Scenario: The crates cargo vet finds unaudited are the ones skipped
    When cargo vet reports "  zeroize:1.9.1 missing [\"safe-to-deploy\"]" and "  syn:3.0.9 missing [\"safe-to-run\"]"
    Then the crates skipped are "syn, zeroize"

  Scenario: The upgrades tried are cargo's, without its downgrades or the pub* crates
    When cargo update --dry-run says "Updating syn v3.0.5 -> v3.0.9", "Downgrading icu_provider v2.3.1 -> v2.1.1 (available: v2.3.1)" and "Updating publet-core v0.1.2 -> v0.1.3"
    Then the upgrades tried are "syn 3.0.5 -> 3.0.9"

  Scenario: A skipped crate's reason is the audits cargo vet recommends
    When a failing cargo vet ends with "recommended audits for safe-to-deploy:" and "cargo vet diff zeroize 1.9.0 1.9.1"
    Then the reason starts at "recommended audits" and names "cargo vet diff zeroize 1.9.0 1.9.1"

  Scenario: The body lists every skipped crate with what cargo vet suggest says
    When the nightly writes a body with "zeroize" skipped because "cargo vet diff zeroize 1.8.2 1.9.1"
    Then the body says nothing merges it
    And the body shows "zeroize 1.9.0 -> 1.9.1" with "cargo vet diff zeroize 1.8.2 1.9.1"

  Scenario: A repository takes every language's updates and fuzzes Rust unless it says otherwise
    Then the settings are update "rust, node, python" and fuzz "rust"
    When the repository's pubkit.json reads '{"kind": "utility", "update": {"node": false}, "security": {"fuzz": {"rust": false}}}'
    Then the settings are update "rust, python" and fuzz "none"
    When the repository's pubkit.json reads '{"update": false}'
    Then the settings are update "none" and fuzz "rust"

  Scenario: A setting pubkit does not know stops the nightly
    When the repository's pubkit.json reads '{"update": {"go": true}}'
    Then reading the nightly settings stops with "update has no language go"
    When the repository's pubkit.json reads '{"update": {"node": "no"}}'
    Then reading the nightly settings stops with "update.node must be true or false"
    When the repository's pubkit.json reads '{"update": 1}'
    Then reading the nightly settings stops with "update must be an object"
    When the repository's pubkit.json reads '{"security": {"advisories": false}}'
    Then reading the nightly settings stops with "the advisory checks always run"
    When the repository's pubkit.json reads '{"security": []}'
    Then reading the nightly settings stops with "security must be an object"
    When the repository's pubkit.json reads '{"security": {"fuzz": {"python": true}}}'
    Then reading the nightly settings stops with "no fuzz job for python yet"

  Scenario: A repository with no npm or Python lock gets no npm or Python updates
    Then the npm and Python updates are empty

  Scenario: The npm updates listed are the direct dependencies that moved
    Given a package-lock.json where "marked" is 18.1.0 and "entities" is 4.5.0
    Then the npm packages listed are "marked@18.1.0"
    And the packages that moved from "marked@18.1.0, yaml@2.9.1" to "marked@18.2.0, yaml@2.9.1" are "marked 18.1.0 -> 18.2.0"

  Scenario: The body of a repository that takes no updates says so
    When the nightly writes a body for a repository that takes no updates
    Then the body says "takes no third-party updates" and lists no "marked"

  Scenario: The body lists the npm updates under their own heading
    When the nightly writes a body with npm updates
    Then the body has the section "## 2. npm packages" with "marked 18.1.0 -> 18.2.0" and no "Python packages"

  Scenario: The nightly commits the npm and Python updates, each on its own
    Given a git repository with a package-lock.json and a Python lock
    When the nightly runs with npm moving "marked" to 18.2.0 and the lock moving "pytest" to 9.1.0
    Then it made the commits "npm python"
    And the body lists "marked 18.1.0 -> 18.2.0" and "pytest 9.0.0 -> 9.1.0"

  Scenario: A repository that turns updates off gets no dependency commits
    Given a git repository with a package-lock.json and a Python lock
    And its pubkit.json turns every language's updates off
    When the nightly runs with npm moving "marked" to 18.2.0 and the lock moving "pytest" to 9.1.0
    Then it made no commits
    And its body says "takes no third-party updates" and lists no "marked"

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
