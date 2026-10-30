Feature: The nightly keeps every pub* repository current, through a pull request
  `pubkit pins` moves every outdated pub* pin, `pubkit crates` keeps only
  the crate updates cargo vet audits and cargo deny passes, and `pubkit
  nightly` commits each, writing the pull request's body. The nightly
  workflow keeps one pull request per repository and never merges it.

  Background:
    Given an empty repository

  Scenario: pins moves every workflow pin to the latest releases
    Given the file ".github/workflows/ci.yml" reads "uses: example-org/pubrel/.github/workflows/release.yml@v0.5.1 with: { pubrel-version: v0.5.1, pub-version: v0.2.0 }"
    When I run pubkit "pins" "--pubrel" "0.5.3" "--pub" "0.2.1" "--no-lock"
    Then it succeeds
    And it prints "ci.yml: pubrel release workflow 0.5.1 -> 0.5.3"
    And it prints "ci.yml: pub-version 0.2.0 -> 0.2.1"
    And ".github/workflows/ci.yml" contains "release.yml@v0.5.3 with: { pubrel-version: v0.5.3, pub-version: v0.2.1 }"

  Scenario: pins moves pubkit's pin in every one of its reusable workflows
    Given the file ".github/workflows/ci.yml" reads "uses: example-org/pubkit/.github/workflows/node-ci.yml@v0.0.1"
    When I run pubkit "pins" "--pubrel" "0.5.3" "--pub" "0.2.1" "--no-lock"
    Then it succeeds
    And ".github/workflows/ci.yml" does not contain "node-ci.yml@v0.0.1"

  Scenario: pins moves pubrel's commit together with its version
    Given pubrel's repository tags "v0.5.3"
    And the file ".github/workflows/ci.yml" reads "    uses: example-org/pubrel/.github/workflows/release.yml@1a49eb98c9647c1b60073d55b9ca0daf3eeee5c0 # v0.5.1"
    When I run pubkit "pins" "--pubrel" "0.5.3" "--pub" "0.2.1" "--no-lock"
    Then it succeeds
    And it prints "ci.yml: pubrel release workflow 0.5.1 -> 0.5.3"
    And ".github/workflows/ci.yml" pins pubrel's release workflow to the commit of "v0.5.3"

  Scenario: pins moves pubkit's commit together with its version
    Given pubkit is a release whose tag names "0f3574a72707a46bae149d3ed7a4644b4837795a"
    And the file ".github/workflows/ci.yml" reads "    uses: example-org/pubkit/.github/workflows/node-ci.yml@1a49eb98c9647c1b60073d55b9ca0daf3eeee5c0 # v0.0.1"
    When I run pubkit "pins" "--pubrel" "0.5.3" "--pub" "0.2.1" "--no-lock"
    Then it succeeds
    And ".github/workflows/ci.yml" pins pubkit's workflows to "0f3574a72707a46bae149d3ed7a4644b4837795a"

  Scenario: pins moves a current pin by tag to the tag's commit
    Given pubrel's repository tags "v0.5.3"
    And the file ".github/workflows/ci.yml" reads "    uses: example-org/pubrel/.github/workflows/release.yml@v0.5.3"
    When I run pubkit "pins" "--pubrel" "0.5.3" "--pub" "0.2.1" "--no-lock"
    Then it succeeds
    And it prints "ci.yml: pubrel release workflow 0.5.3 pinned to"
    And ".github/workflows/ci.yml" pins pubrel's release workflow to the commit of "v0.5.3"

  Scenario: pins moves a workflow pinned under the old owner to workflows_owner's
    Given pubkit.json names the workflows owner "sustenaut"
    And the file ".github/workflows/ci.yml" reads "    uses: example-org/pubkit/.github/workflows/node-ci.yml@v0.0.1"
    When I run pubkit "pins" "--pubrel" "0.5.3" "--pub" "0.2.1" "--no-lock"
    Then it succeeds
    And ".github/workflows/ci.yml" contains "uses: sustenaut/pubkit/.github/workflows/node-ci.yml@v"
    And ".github/workflows/ci.yml" does not contain "example-org"

  Scenario Outline: pins moves a pubkit action to this pubkit's commit, under the current owner
    Given pubkit is a release whose tag names "0f3574a72707a46bae149d3ed7a4644b4837795a"
    And the workflow "deploy.yml" uses "<owner>"'s pubkit action "deploy-static" at "<pin>"
    When I run pubkit "pins" "--pubrel" "0.5.3" "--pub" "0.2.1" "--no-lock"
    Then it succeeds
    And ".github/workflows/deploy.yml" uses "Sustenaut"'s pubkit action "deploy-static" at "this pubkit's commit"

    Examples:
      | owner     | pin               |
      | Sustenaut | another pubkit    |
      | example-org   | another pubkit    |
      | Sustenaut | this pubkit's tag |
      | Sustenaut | another commit    |

  Scenario Outline: pins moves a hand-written call of any pubkit workflow to this pubkit's commit
    Given pubkit is a release whose tag names "0f3574a72707a46bae149d3ed7a4644b4837795a"
    And the workflow "ci.yml" calls "<owner>"'s pubkit workflow "<workflow>" at "another pubkit"
    When I run pubkit "pins" "--pubrel" "0.5.3" "--pub" "0.2.1" "--no-lock"
    Then it succeeds
    And ".github/workflows/ci.yml" calls "Sustenaut"'s pubkit workflow "<workflow>" at "this pubkit's commit"

    Examples:
      | owner     | workflow       |
      | Sustenaut | service-ci.yml |
      | example-org   | service-ci.yml |
      | Sustenaut | job-sonar.yml  |

  Scenario: pins leaves a pubkit action that names no version
    Given pubkit is a release whose tag names "0f3574a72707a46bae149d3ed7a4644b4837795a"
    And the workflow "deploy.yml" uses "Sustenaut"'s pubkit action "deploy-static" at "main"
    When I run pubkit "pins" "--pubrel" "0.5.3" "--pub" "0.2.1" "--no-lock"
    Then it succeeds
    And ".github/workflows/deploy.yml" uses "Sustenaut"'s pubkit action "deploy-static" at "main"

  Scenario: pins on a repository already current changes nothing
    Given the file ".github/workflows/ci.yml" reads "uses: example-org/pubrel/.github/workflows/release.yml@v0.5.3"
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

  Scenario: A repository takes every language's updates and fuzzing unless it says otherwise
    Then the settings are update "rust, node, python" and fuzz "rust, node, python"
    When the repository's pubkit.json reads '{"kind": "utility", "update": {"node": false}, "security": {"fuzz": {"rust": false}}}'
    Then the settings are update "rust, python" and fuzz "node, python"
    When the repository's pubkit.json reads '{"update": false, "security": {"fuzz": false}}'
    Then the settings are update "none" and fuzz "none"

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
    When the repository's pubkit.json reads '{"security": {"fuzz": {"go": true}}}'
    Then reading the nightly settings stops with "security.fuzz has no language go"

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

  Scenario: The nightly pushes as its GitHub App, one repository at a time
    When I parse the workflow "nightly.yml"
    Then each repository's token comes from the Sustenaut nightly app, for that repository alone
    And the token may write only contents, pull requests and workflows, and read actions
    And without the app it falls back to PUBKIT_BUMP_TOKEN with a warning

  Scenario: pins moves the crates pubkit.json names, and no others
    Given the file "pubkit.json" reads "{"nightly": {"pins": {"crates": ["my-core"]}}}"
    And Cargo.lock locks "my-core, publet-core"
    And cargo stands in, recording what it is asked
    When I run pubkit "pins" "--pub" "0.2.1" "--no-lock"
    Then it succeeds
    And cargo was asked to update "my-core" alone
    And stderr does not mention "nightly.pins.crates"

  Scenario: pins moves no crates when pubkit.json names none
    Given the file "pubkit.json" reads "{"nightly": {"pins": {"crates": []}}}"
    And Cargo.lock locks "publet-core"
    And cargo stands in, recording what it is asked
    When I run pubkit "pins" "--pub" "0.2.1" "--no-lock"
    Then it succeeds
    And cargo was asked to update nothing

  Scenario: Without nightly.pins.crates, pins moves the old crates and warns
    Given Cargo.lock locks "publet-core, serde"
    And cargo stands in, recording what it is asked
    When I run pubkit "pins" "--pub" "0.2.1" "--no-lock"
    Then it succeeds
    And cargo was asked to update "publet-core" alone
    And stderr mentions "pubkit.json names no nightly.pins.crates, so publet-core move as pins"

  Scenario: A repository with none of the old crates hears no warning
    Given Cargo.lock locks "serde"
    And cargo stands in, recording what it is asked
    When I run pubkit "pins" "--pub" "0.2.1" "--no-lock"
    Then it succeeds
    And stderr does not mention "nightly.pins.crates"

  Scenario: The upgrades tried leave out the crates pubkit.json pins, and only those
    Given the file "pubkit.json" reads "{"nightly": {"pins": {"crates": ["my-core"]}}}"
    When cargo update --dry-run here says "Updating my-core v1.0.0 -> v1.0.1" and "Updating publet-core v1.0.0 -> v1.0.1"
    Then the upgrades tried are "publet-core 1.0.0 -> 1.0.1"

  Scenario Outline: sync --check refuses a nightly block pubkit cannot use, naming the key
    Given the file "pubkit.json" reads "{"kind": "utility", "nightly": <block>}"
    When I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "<message>"

    Examples:
      | block                                | message                                |
      | {"pin": {}}                          | nightly has no setting pin             |
      | {"pins": {"crate": ["a"]}}           | nightly.pins has no setting crate      |
      | {"pins": {"crates": "a"}}            | nightly.pins.crates must list crate names |
      | {"pins": {"crates": ["a b"]}}        | nightly.pins.crates must list crate names |
      | {"pins": []}                         | nightly.pins must be an object         |
      | []                                   | nightly must be an object              |

  Scenario: pubkit new names the owner it is given, and the protect block the repository needs
    When I start the package "pubfoo" owned by "example-org"
    Then "Cargo.toml" contains "https://github.com/example-org/pubfoo"
    And "SECURITY.md" contains "https://github.com/example-org/pubfoo/security"
    And what it printed says "add a protect block" and "switch the repository on in the release app"
    And what it printed does not say "protected.json" or "governed.json"

  Scenario: pubkit new takes the owner from the origin remote
    Given the origin remote is "git@github.com:example-org/pubfoo.git"
    When I start the package "pubfoo" without a key
    Then "Cargo.toml" contains "https://github.com/example-org/pubfoo"

  Scenario: pubkit new without an owner or a GitHub origin stops, asking for one
    When I run pubkit "new" "--name=pubfoo" "--tag=PKG-PUBFOO-10-2026" "--no-key" "--no-lock"
    Then it fails
    And stderr mentions "--owner=OWNER"
    And the repository has no "Cargo.toml"

  Scenario: pubkit new uses no key whose identifier pub prints wrongly
    Given pub stands in, printing the key "--root=elsewhere"
    When I start the package "pubfoo" with a key, owned by "example-org"
    Then it stops, saying "is not an object's identifier"
    And pub was not asked to set the policy

  Scenario: The identifier pubkit new takes is the one pubkit makes
    Then an object's identifier passes as a key

  Scenario: pubkit new refuses an owner GitHub could not have
    When I run pubkit "new" "--name=pubfoo" "--tag=PKG-PUBFOO-10-2026" "--owner=a/b" "--no-key" "--no-lock"
    Then it fails
    And stderr mentions "--owner must be a GitHub owner"

  Scenario: The pub pubkit new asks for comes from the one place that says where
    When the tools are checked with no pub installed
    Then the install line names "https://github.com/Sustenaut/publet"

  Scenario: pubkit new writes a package that conforms but for its release key
    Given the origin remote is "https://github.com/example-org/pubfoo"
    When I start the package "pubfoo" without a key
    Then the repository has "Cargo.toml", "src/lib.rs", "release.json", "SECURITY.md", "TESTING.md", "SUPPLY-CHAIN.md", "fuzz/fuzz_targets/greet.rs", ".gitignore"
    And it conforms to the standard but for "RS-SUP-05"
