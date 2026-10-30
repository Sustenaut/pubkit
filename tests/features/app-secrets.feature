Feature: pubkit app-secrets sends one GitHub App's three values to this repository
  The repository's pubkit.json names them, so pubkit names nothing of anyone's.
  It checks all three, and writes none unless all pass. It never prints a value,
  and never puts one on a command line.

  Scenario: Three good values are sent to the repository origin points at
    Given a clone of "example-org/thing" whose github_app block names an app
    And Bitwarden has all three values
    When I run pubkit "app-secrets"
    Then it succeeds
    And gh set the variable "THE_APP_ID" in "example-org/thing"
    And gh set the variable "THE_APP_INSTALLATION_ID" in "example-org/thing"
    And gh set the secret "THE_APP_KEY" in "example-org/thing"
    And gh was called 3 times
    And no value is printed or on a command line

  Scenario: --check sends nothing
    Given a clone of "example-org/thing" whose github_app block names an app
    And Bitwarden has all three values
    When I run pubkit "app-secrets" "--check"
    Then it succeeds
    And it prints "ok       the-app-key ("
    And it prints "--check: nothing was written."
    And gh was called 0 times
    And no value is printed or on a command line

  Scenario Outline: Anything wrong with one stops all three, and says which
    Given a clone of "example-org/thing" whose github_app block names an app
    And Bitwarden has all three values
    But Bitwarden's "<name>" is <what>
    When I run pubkit "app-secrets"
    Then the exit code is 1
    And it prints "<says>"
    And gh was called 0 times

    Examples:
      | name                    | what       | says                                    |
      | the-app-id              | missing    | MISSING  the-app-id                     |
      | the-app-key             | missing    | MISSING  the-app-key                    |
      | the-app-id              | empty      | BAD      the-app-id is empty            |
      | the-app-id              | "12ab"     | BAD      the-app-id is not an id        |
      | the-app-installation-id | "٣"        | BAD      the-app-installation-id is not an id |
      | the-app-key             | "nonsense" | BAD      the-app-key is not a private key |

  Scenario: A failed upload is reported, and fails the command
    Given a clone of "example-org/thing" whose github_app block names an app
    And Bitwarden has all three values
    And gh refuses "THE_APP_ID"
    When I run pubkit "app-secrets"
    Then the exit code is 1
    And it prints "FAILED   variable THE_APP_ID in example-org/thing"
    And gh set the secret "THE_APP_KEY" in "example-org/thing"

  Scenario: A repository without the block is told so
    Given the file "pubkit.json" reads "{"kind": "utility"}"
    When I run pubkit "app-secrets"
    Then the exit code is 1
    And it prints "pubkit.json has no github_app block"

  Scenario Outline: pubkit sync and the command both refuse a bad block
    Given the file "pubkit.json" reads "{"kind": "utility", "github_app": <block>}"
    When I run pubkit "app-secrets"
    Then the exit code is 1
    And it prints "<message>"
    When I run pubkit "sync" "--check"
    Then the exit code is 1

    Examples:
      | block                                                                                                                      | message                       |
      | []                                                                                                                         | it must be an object          |
      | {}                                                                                                                         | id needs "from"               |
      | {"id": {"from": "a", "to": "A"}}                                                                                           | installation_id needs "from"  |
      | {"id": {"from": "a", "to": "A"}, "installation_id": {"from": "b", "to": "B"}, "key": {"from": "c", "to": "C"}, "extra": 1} | extra is not a setting        |
      | {"id": {"from": "a b", "to": "A"}, "installation_id": {"from": "b", "to": "B"}, "key": {"from": "c", "to": "C"}}           | id's "from" is not            |
      | {"id": {"from": "a", "to": "lower"}, "installation_id": {"from": "b", "to": "B"}, "key": {"from": "c", "to": "C"}}         | id's "to" must be             |
      | {"id": {"from": "a", "to": "GITHUB_X"}, "installation_id": {"from": "b", "to": "B"}, "key": {"from": "c", "to": "C"}}      | id's "to" must be             |
      | {"id": {"from": "a", "to": "A"}, "installation_id": {"from": "b", "to": "A"}, "key": {"from": "c", "to": "C"}}             | two of them have the same     |
      | {"id": {"from": 1, "to": 2}, "installation_id": {"from": "b", "to": "B"}, "key": {"from": "c", "to": "C"}}                 | id's "from" is not            |

  Scenario: pubkit sync takes a sound block
    Given a repository initialised as "utility"
    And its pubkit.json has a sound github_app block
    When I run pubkit "sync" "--check"
    Then it prints "in step"
