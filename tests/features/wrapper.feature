Feature: ./pubrules runs the repository's pinned pubrules
  Every repository gets a managed `pubrules` script that runs exactly the
  pubrules its managed files and lock pin, through uvx and the release's wheel,
  whichever pubrules (if any) is installed. It runs in the repository's root,
  from whichever directory it is called.

  Scenario: init writes the wrapper, pinned to this pubrules
    Given an empty repository
    When I run pubrules "init" "--kind=utility"
    Then it succeeds
    And the repository has "pubrules"
    And "pubrules" is executable
    And "pubrules" runs this pubrules' release wheel

  Scenario: The wrapper is managed, so an edited copy fails the check
    Given a repository initialised as "utility"
    And the file "pubrules" reads "edited"
    When I run pubrules "sync" "--check"
    Then it fails
    And stderr mentions "pubrules differs"
    When I run pubrules "sync"
    Then it succeeds
    And "pubrules" is executable

  Scenario: It runs the pinned release with the arguments given, in the repository's root
    Given a repository initialised as "utility"
    And uvx records how it was run
    When I run "../pubrules dns check example.eu" from the directory "docs"
    Then it succeeds
    And uvx ran pubrules from this pubrules' release wheel with "dns check example.eu"
    And uvx ran in the repository's root

  Scenario: Without uv it says how to install it
    Given a repository initialised as "utility"
    When I run "./pubrules sync" without uv
    Then the exit code is 127
    And stderr mentions "./pubrules runs pubrules"
    And stderr mentions "https://astral.sh/uv/install.sh"
