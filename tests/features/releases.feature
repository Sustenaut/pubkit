Feature: Other packages' releases are each repository's own pins
  pubrules holds no version of pubrel, pubtest, pubsec or pubdeploy. A
  repository pins each: pubrel in ci.release.pubrel or the release workflow
  sync wrote, pubtest in tests/requirements.txt, pubsec and pubdeploy in
  deploy.providers or the launcher sync wrote. Where it has no pin yet, as
  when `pubrules new` starts it, the pin is the release the release state
  (PUBRULES_RELEASES) lists, or else the package's latest GitHub release; the
  nightly then moves each pin as it moves any other. Every scenario's release
  state lists pubrel 0.7.0, pubtest 0.2.2, pubsec 0.3.0 and pubdeploy 0.5.0.

  Background:
    Given an empty repository

  Scenario: A new package pins pubrel and pubtest at the releases the release state lists
    Given the release state lists "pubrel" at "0.8.0"
    And the release state lists "pubtest" at "0.2.3"
    When I start the package "pubfoo"
    Then it succeeds
    And the JSON in "pubrules.json" has "ci.release.pubrel" equal to "0.8.0"
    And the file ".github/workflows/pr.yml" includes "pubrel/.github/workflows/release.yml@v0.8.0"
    And the file "tests/requirements.txt" includes "pubtest @ https://github.com/Sustenaut/pubtest/releases/download/v0.2.3/pubtest-0.2.3-py3-none-any.whl"
    And the file "tests/requirements-pubrules.txt" includes "# pubtest floor: 0.2.2"
    And the file "tests/requirements-pubrules.txt" does not include "pubtest @"
    And the file "tests/requirements-pubrules.txt" does not include "pubtest>="

  Scenario: Where the release state lists none, a new package pins pubrel's latest GitHub release
    Given the release state lists no "pubrel"
    And GitHub's latest release of "Sustenaut/pubrel" is "v0.8.1"
    When I start the package "pubfoo"
    Then it succeeds
    And the JSON in "pubrules.json" has "ci.release.pubrel" equal to "0.8.1"

  Scenario: With neither, a new package stops, saying where it looked
    Given the release state lists no "pubrel"
    And GitHub has no release of "Sustenaut/pubrel"
    When I start the package "pubfoo"
    Then it is refused, saying "no release of pubrel to pin: neither PUBRULES_RELEASES's releases block nor Sustenaut/pubrel's latest GitHub release names one"

  Scenario: A release state entry that is not a version stops pubrules
    Given the release state lists "pubrel" at "latest"
    When I start the package "pubfoo"
    Then it is refused, saying "releases.pubrel must be a version"

  Scenario: Sync keeps the pubrel the workflows pin where ci.release names none
    Given a repository initialised as "utility"
    And the release state lists "pubrel" at "0.9.0"
    When I run pubrules "sync"
    Then it succeeds
    And the file ".github/workflows/pr.yml" includes "pubrel/.github/workflows/release.yml@v0.7.0"
    And the file ".github/workflows/pr.yml" does not include "v0.9.0"

  Scenario: The nightly writes the pubrel the release job calls into ci.release, and moves it
    Given a repository initialised as "utility"
    When I run pubrules "pins" "--pubrel" "0.8.0" "--no-lock"
    Then it succeeds
    And it prints "pubrules.json: ci.release.pubrel 0.7.0 -> 0.8.0"
    And the JSON in "pubrules.json" has "ci.release.pubrel" equal to "0.8.0"
    And the file ".github/workflows/pr.yml" includes "pubrel/.github/workflows/release.yml@v0.8.0"

  Scenario: Sync check fails where tests/requirements.txt pins no pubtest, and sync pins the lock's
    Given a repository initialised as "utility"
    And a file "tests/requirements.txt" holding "-r requirements-pubrules.txt"
    And a file "tests/requirements.lock" holding "pubtest @ https://github.com/Sustenaut/pubtest/releases/download/v0.2.4/pubtest-0.2.4-py3-none-any.whl"
    When I run pubrules "sync" "--check"
    Then it fails
    And stderr mentions "tests/requirements.txt does not pin pubtest by its release wheel"
    When I run pubrules "sync"
    Then it succeeds
    And it prints "updated  tests/requirements.txt (pubtest: 0.2.4)"
    And the file "tests/requirements.txt" holds:
      """
      # The steps and plugin the suite runs with; the nightly moves the pin.
      pubtest @ https://github.com/Sustenaut/pubtest/releases/download/v0.2.4/pubtest-0.2.4-py3-none-any.whl
      -r requirements-pubrules.txt
      """
    And the pubtest tests/requirements.txt pins is one tests/requirements-pubrules.txt takes

  Scenario: A lock of an older pubrules, whose pubtest this pubrules no longer takes, gives no pin: sync pins the release state's
    A bump from pubrules 0.45.0 kept such a lock's pubtest 0.2.1 beside
    a floor of 0.2.2, which no lock resolves.
    Given a repository initialised as "utility"
    And a file "tests/requirements.txt" holding "-r requirements-pubrules.txt"
    And a file "tests/requirements.lock" holding "pubtest @ https://github.com/Sustenaut/pubtest/releases/download/v0.2.1/pubtest-0.2.1-py3-none-any.whl"
    When I run pubrules "sync"
    Then it succeeds
    And it prints "updated  tests/requirements.txt (pubtest: 0.2.2)"
    And the file "tests/requirements.txt" includes "pubtest @ https://github.com/Sustenaut/pubtest/releases/download/v0.2.2/pubtest-0.2.2-py3-none-any.whl"
    And the pubtest tests/requirements.txt pins is one tests/requirements-pubrules.txt takes

  Scenario: The nightly moves the pubtest tests/requirements.txt pins to its latest release
    Given a repository initialised as "utility"
    And GitHub's latest release of "Sustenaut/pubtest" is "v0.2.5"
    When the nightly moves the repository's pins
    Then the moves include "tests/requirements.txt: Sustenaut/pubtest 0.2.2 -> 0.2.5"
    And the file "tests/requirements.txt" includes "pubtest @ https://github.com/Sustenaut/pubtest/releases/download/v0.2.5/pubtest-0.2.5-py3-none-any.whl"

  Scenario: The nightly moves the pubsec a service's launcher runs, and its sha256
    Given a service repository
    And GitHub's latest release of "Sustenaut/pubsec" is "v0.4.0"
    And each wheel fetched hashes to "4444444444444444444444444444444444444444444444444444444444444444"
    When the nightly moves the repository's pins
    Then the moves include "pubsec: Sustenaut/pubsec 0.3.0 -> 0.4.0"
    And the file "pubsec" includes "fetch https://github.com/Sustenaut/pubsec/releases/download/v0.4.0/pubsec-0.4.0-py3-none-any.whl 4444444444444444444444444444444444444444444444444444444444444444 providers deploy.providers"

  Scenario: A release whose wheel is named by another distribution is pinned by that name
    pubtest's repository releases its wheel as pubtestrules, its PyPI name.
    Given the release state lists no "pubtest"
    And GitHub's latest release of "Sustenaut/pubtest" is "v0.3.1"
    And release "v0.3.1" of "Sustenaut/pubtest" carries "pubtestrules-0.3.1-py3-none-any.whl"
    When I start the package "pubfoo"
    Then it succeeds
    And the file "tests/requirements.txt" includes "pubtestrules @ https://github.com/Sustenaut/pubtest/releases/download/v0.3.1/pubtestrules-0.3.1-py3-none-any.whl"

  Scenario: The release state can name a release's wheel
    Given the release state lists "pubtest" with the wheel "pubtestrules-0.3.1-py3-none-any.whl" at "0.3.1"
    And a repository initialised as "utility"
    And a file "tests/requirements.txt" holding "-r requirements-pubrules.txt"
    When I run pubrules "sync"
    Then it succeeds
    And the file "tests/requirements.txt" includes "pubtestrules @ https://github.com/Sustenaut/pubtest/releases/download/v0.3.1/pubtestrules-0.3.1-py3-none-any.whl"

  Scenario: The nightly follows a pubtest release whose wheel is named by another distribution
    Given a repository initialised as "utility"
    And GitHub's latest release of "Sustenaut/pubtest" is "v0.3.1"
    And release "v0.3.1" of "Sustenaut/pubtest" carries "pubtestrules-0.3.1-py3-none-any.whl"
    When the nightly moves the repository's pins
    Then the moves include "tests/requirements.txt: Sustenaut/pubtest 0.2.2 -> 0.3.1"
    And the file "tests/requirements.txt" includes "pubtestrules @ https://github.com/Sustenaut/pubtest/releases/download/v0.3.1/pubtestrules-0.3.1-py3-none-any.whl"
    And the file "tests/requirements.txt" does not include "pubtest @"
    And the pubtest tests/requirements.txt pins is one tests/requirements-pubrules.txt takes

  Scenario: A release that names no wheel keeps the name the pin had
    Given a repository initialised as "utility"
    And GitHub's latest release of "Sustenaut/pubtest" is "v0.2.5"
    And release "v0.2.5" of "Sustenaut/pubtest" carries "pubtest-0.2.5.tar.gz"
    When the nightly moves the repository's pins
    Then the file "tests/requirements.txt" includes "pubtest @ https://github.com/Sustenaut/pubtest/releases/download/v0.2.5/pubtest-0.2.5-py3-none-any.whl"

  Scenario: Sync check fails on a pubtest older than pubrules' steps need, and sync pins a newer one
    Given a repository initialised as "utility"
    And a file "tests/requirements.txt" holding:
      """
      # The steps and plugin the suite runs with; the nightly moves the pin.
      pubtest @ https://github.com/Sustenaut/pubtest/releases/download/v0.2.1/pubtest-0.2.1-py3-none-any.whl
      """
    When I run pubrules "sync" "--check"
    Then it fails
    And stderr mentions "tests/requirements.txt pins a pubtest older than the 0.2.2 pubrules' steps need"
    When I run pubrules "sync"
    Then it succeeds
    And the file "tests/requirements.txt" holds:
      """
      # The steps and plugin the suite runs with; the nightly moves the pin.
      pubtest @ https://github.com/Sustenaut/pubtest/releases/download/v0.2.2/pubtest-0.2.2-py3-none-any.whl
      """

  Scenario: A release state entry that names no wheel takes the name the release gives it
    Given the release state lists "pubtest" at "0.3.1" with no wheel
    And release "v0.3.1" of "Sustenaut/pubtest" carries "pubtestrules-0.3.1-py3-none-any.whl"
    When I start the package "pubfoo"
    Then it succeeds
    And the file "tests/requirements.txt" includes "pubtestrules @ https://github.com/Sustenaut/pubtest/releases/download/v0.3.1/pubtestrules-0.3.1-py3-none-any.whl"
