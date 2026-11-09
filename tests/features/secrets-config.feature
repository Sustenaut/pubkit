Feature: pubkit reads what pubkit.json says about secrets, and never a value
  The github_app and secrets blocks name the values a repository needs and
  where they go on GitHub, and signing_keys each signing key and where it is
  backed up. `pubkit sync --check` refuses a block it can't use; the
  commands that send, write or back up those values are pubsec's, and the
  ones that were pubkit's say so.

  Scenario Outline: A wrong secrets list is refused by sync
    Given the file "pubkit.json" reads "{"kind": "utility", "secrets": <list>}"
    When I run pubkit "sync" "--check"
    Then the exit code is 1
    And stderr mentions "<message>"

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

  Scenario Outline: pubkit sync refuses a bad github_app block
    Given the file "pubkit.json" reads "{"kind": "utility", "github_app": <block>}"
    When I run pubkit "sync" "--check"
    Then the exit code is 1
    And stderr mentions "<message>"

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

  Scenario Outline: sync --check refuses a signing_keys block it can't use
    Given a repository initialised as "utility"
    And its pubkit.json's signing_keys is <block>
    When I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "<problem>"

    Examples:
      | block                                                                      | problem                                |
      | [{"path": "k"}]                                                            | needs bitwarden                        |
      | {"path": "k"}                                                              | must be a list                         |
      | [{"path": "/abs/k", "bitwarden": {"project": "p", "name": "n"}}]           | path is from the repository's root     |
      | [{"path": "../k", "bitwarden": {"project": "p", "name": "n"}}]             | path leaves the repository             |
      | [{"path": "k", "bitwarden": {"project": "p", "name": "-n"}}]               | bitwarden.name must be a letter        |
      | [{"path": "k", "bitwarden": {"project": "p", "name": "n"}, "extra": 1}]     | extra is not a setting                 |
      | [{"path": "k", "bitwarden": {"project": {"id": "nope"}, "name": "n"}}]     | must be the project's id, a UUID       |
      | [{"path": "k", "bitwarden": {"project": {"id": "11111111-2222-3333-4444-555555555555", "colour": 1}, "name": "n"}}] | bitwarden.project.colour is not a setting |

  Scenario Outline: A command that moved to pubsec says where, and fails
    Given a repository initialised as "utility"
    When I run pubkit <command>
    Then the exit code is 2
    And stderr mentions "pubkit: this is now ./pubsec <then>; run pubkit sync to get the launcher"

    Examples:
      | command                                              | then                                     |
      | "secrets" "run" "--" "terraform" "plan"              | run -- terraform plan                    |
      | "app-secrets" "--check"                              | app-secrets --check                      |
      | "github-app" "create" "app.json"                     | github-app create app.json               |
      | "service-account-key" "create" "--account" "a@b"     | service-account-key create --account a@b |
      | "signing-key" "backup" "--dry-run"                   | signing-key backup --dry-run             |
