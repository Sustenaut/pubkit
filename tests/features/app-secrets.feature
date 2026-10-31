Feature: pubkit app-secrets sends what this repository names to it
  The repository's pubkit.json names them, so pubkit names nothing of anyone's.
  It checks them all, and writes none unless all pass. It never prints a value,
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

  Scenario: Several apps and other values all go, by their own kinds
    Given a clone of "example-org/thing" that names two apps and two other values
    And Bitwarden has all those values
    When I run pubkit "app-secrets"
    Then it succeeds
    And it prints "Done: 8 values in example-org/thing."
    And gh set the secret "OTHER_APP_KEY" in "example-org/thing"
    And gh set the secret "PLAIN_TOKEN" in "example-org/thing"
    And gh set the variable "PLAIN_USER" in "example-org/thing"
    And gh was called 8 times
    And no value is printed or on a command line

  Scenario: A value can go in an environment, and the same name can be in two
    Given a clone of "example-org/thing" whose secrets list has "PLAIN_TOKEN" in the environment "server", and in the repository
    And Bitwarden has all those values
    When I run pubkit "app-secrets"
    Then it succeeds
    And gh set the secret "PLAIN_TOKEN" in the environment "server" of "example-org/thing"
    And gh set the secret "PLAIN_TOKEN" in "example-org/thing" with no environment
    And it prints "sent     secret PLAIN_TOKEN in example-org/thing, environment server"

  Scenario: A failure says which environment
    Given a clone of "example-org/thing" whose secrets list has "PLAIN_TOKEN" in the environment "server", and in the repository
    And Bitwarden has all those values
    And gh refuses "PLAIN_TOKEN"
    When I run pubkit "app-secrets"
    Then the exit code is 1
    And it prints "FAILED   secret PLAIN_TOKEN in example-org/thing, environment server"

  Scenario: Only other values, with no app, work too
    Given a clone of "example-org/thing" that names only "plain-token" as a secret
    And Bitwarden has all those values
    When I run pubkit "app-secrets" "--check"
    Then it succeeds
    And it prints "ok       plain-token ("
    And it prints "All 1 look right."
    And gh was called 0 times

  Scenario Outline: One other value being wrong stops everything
    Given a clone of "example-org/thing" that names two apps and two other values
    And Bitwarden has all those values
    But Bitwarden's "<name>" is <what>
    When I run pubkit "app-secrets"
    Then the exit code is 1
    And it prints "<says>"
    And gh was called 0 times

    Examples:
      | name        | what    | says                         |
      | plain-token | missing | MISSING  plain-token         |
      | plain-user  | empty   | BAD      plain-user is empty |

  Scenario Outline: A wrong secrets list is refused by the command and by sync
    Given the file "pubkit.json" reads "{"kind": "utility", "secrets": <list>}"
    When I run pubkit "app-secrets"
    Then the exit code is 1
    And it prints "<message>"
    When I run pubkit "sync" "--check"
    Then the exit code is 1

    Examples:
      | list                                                    | message                   |
      | {}                                                      | it must be a list         |
      | [1]                                                     | needs "from"              |
      | [{"from": "a", "to": "A"}]                              | needs "from"              |
      | [{"from": "a b", "to": "A", "kind": "secret"}]          | A's "from" is not         |
      | [{"from": "a", "to": "low", "kind": "secret"}]          | low's "to" must be        |
      | [{"from": "a", "to": "A", "kind": "plain"}]             | A's "kind" must be        |
      | [{"from": "a", "to": "A", "kind": 1}]                   | A's "kind" must be        |
      | [{"from": "a", "to": "A", "kind": "secret", "environment": "a b"}] | A's "environment" is not |
      | [{"from": "a", "to": "A", "kind": "secret", "environment": 1}] | A's "environment" is not |
      | [{"from": "a", "to": "A", "kind": "secret", "extra": 1}] | needs "from"              |
      | [{"from": "a", "to": "A", "kind": "secret", "environment": "e"}, {"from": "b", "to": "A", "kind": "secret", "environment": "e"}] | two of them have the same |
      | [{"from": 1, "to": 2, "kind": "secret"}]                | 2's "from" is not         |
      | [{"from": "a", "to": "A", "kind": "secret"}, {"from": "b", "to": "A", "kind": "secret"}] | two of them have the same |

  Scenario: An empty secrets list has nothing to send
    Given the file "pubkit.json" reads "{"kind": "utility", "secrets": []}"
    When I run pubkit "app-secrets"
    Then the exit code is 1
    And it prints "secrets list is empty"

  Scenario: An empty list of apps is refused
    Given the file "pubkit.json" reads "{"kind": "utility", "github_app": []}"
    When I run pubkit "app-secrets"
    Then the exit code is 1
    And it prints "it lists no apps"

  Scenario: A repository without the block is told so
    Given the file "pubkit.json" reads "{"kind": "utility"}"
    When I run pubkit "app-secrets"
    Then the exit code is 1
    And it prints "pubkit.json has no github_app block or secrets list"

  Scenario Outline: pubkit sync and the command both refuse a bad block
    Given the file "pubkit.json" reads "{"kind": "utility", "github_app": <block>}"
    When I run pubkit "app-secrets"
    Then the exit code is 1
    And it prints "<message>"
    When I run pubkit "sync" "--check"
    Then the exit code is 1

    Examples:
      | block                                                                                                                      | message                       |
      | 1                                                                                                                          | it must be an object          |
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
