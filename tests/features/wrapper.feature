Feature: ./pubkit runs the repository's pinned pubkit
  Every repository gets a managed `pubkit` script that runs exactly the
  pubkit its managed files and lock pin, through uvx and the release's wheel,
  whichever pubkit (if any) is installed. It runs in the repository's root,
  from whichever directory it is called.

  Scenario: init writes the wrapper, pinned to this pubkit
    Given an empty repository
    When I run pubkit "init" "--kind=utility"
    Then it succeeds
    And the repository has "pubkit"
    And "pubkit" is executable
    And "pubkit" runs this pubkit's release wheel

  Scenario: The wrapper is managed, so an edited copy fails the check
    Given a repository initialised as "utility"
    And the file "pubkit" reads "edited"
    When I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "pubkit differs"
    When I run pubkit "sync"
    Then it succeeds
    And "pubkit" is executable

  Scenario: It runs the pinned release with the arguments given, in the repository's root
    Given a repository initialised as "utility"
    And uvx records how it was run
    When I run "../pubkit dns check example.eu" from the directory "docs"
    Then it succeeds
    And uvx ran pubkit from this pubkit's release wheel with "dns check example.eu"
    And uvx ran in the repository's root

  Scenario: Without uv it says how to install it
    Given a repository initialised as "utility"
    When I run "./pubkit sync" without uv
    Then the exit code is 127
    And stderr mentions "./pubkit runs pubkit"
    And stderr mentions "https://astral.sh/uv/install.sh"
