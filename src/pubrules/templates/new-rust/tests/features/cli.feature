Feature: The {name} command
  Scenario: --version prints the version
    When I run {name} "--version"
    Then it succeeds
    And it prints "{name} 0.1.0"

  Scenario: A name is greeted
    When I run {name} "publet"
    Then it prints "hello, publet"

  Scenario: No arguments prints the usage and fails
    When I run {name} with no arguments
    Then it fails
    And stderr mentions "usage: {name}"
