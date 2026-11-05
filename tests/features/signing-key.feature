Feature: pubkit signing-key backup copies the signing keys repositories declare to Bitwarden
  A repository names each signing key it uses, and the Bitwarden project and
  secret it is backed up to, in pubkit.json's signing_keys. `pubkit
  signing-key backup` writes each declared key that is on this machine and not
  yet in its project, from memory, with the token typed at a hidden prompt. It
  never overwrites a secret, and never prints a key.

  Scenario: A dry run says what it would back up, and asks for nothing
    Given a repository that declares its corpus key as "pub-signing-key-demo" in the project "the-project"
    When I run pubkit "signing-key" "backup" "--dry-run"
    Then it succeeds
    And it prints "pub-signing-key-demo: would back up"
    And nothing was asked of Bitwarden

  Scenario: A declared key Bitwarden lacks is written, and never printed
    Given a repository that declares its corpus key as "pub-signing-key-demo" in the project "the-project"
    When I back the keys up, giving the token "tok"
    Then it succeeds
    And the SDK created "pub-signing-key-demo" holding the corpus key
    And it prints "pub-signing-key-demo: backed up"
    And neither the key nor the token is in what it printed

  Scenario: A key already backed up with the same value is left alone
    Given a repository that declares its corpus key as "pub-signing-key-demo" in the project "the-project"
    And the project already holds the corpus key as "pub-signing-key-demo"
    When I back the keys up, giving the token "tok"
    Then it succeeds
    And it prints "pub-signing-key-demo: already backed up"
    And the SDK wrote nothing

  Scenario: Another value under the same name is never overwritten
    Given a repository that declares its corpus key as "pub-signing-key-demo" in the project "the-project"
    And the project already holds "another key" as "pub-signing-key-demo"
    When I back the keys up, giving the token "tok"
    Then it fails
    And it prints "holds another value under this name; not overwritten"
    And the SDK wrote nothing

  Scenario: A corpus key pubkit.json does not declare is pointed out
    Given a repository with a corpus key it does not declare
    When I run pubkit "signing-key" "backup" "--dry-run"
    Then it succeeds
    And it prints "a signing key pubkit.json does not declare"

  Scenario: A key outside the repository is declared with a path from home
    Given a repository that declares "~/.config/demo/facts.key" as "pub-signing-key-facts" in the project "the-project"
    When I back the keys up, giving the token "tok"
    Then it succeeds
    And the SDK created "pub-signing-key-facts" holding the home key

  Scenario: Two checkouts with the same key back it up once, and with different keys not at all
    Given two checkouts under one directory that declare their corpus keys as "pub-signing-key-demo"
    When I back up every repository under that directory, giving the token "tok"
    Then it succeeds
    And the SDK created "pub-signing-key-demo" once
    Given the second checkout's corpus key differs
    When I back up every repository under that directory, giving the token "tok"
    Then it fails
    And it prints "pub-signing-key-demo: different keys at"

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

  Scenario: With no token typed, nothing is written
    Given a repository that declares its corpus key as "pub-signing-key-demo" in the project "the-project"
    When I back the keys up, giving the token ""
    Then it fails
    And it prints "no token given; nothing written"
    And the SDK wrote nothing

  Scenario: A declared key that is not on this machine is reported, not failed
    Given a repository that declares a key at "~/.config/demo/missing.key" as "pub-signing-key-gone"
    When I run pubkit "signing-key" "backup" "--dry-run"
    Then it succeeds
    And it prints "pub-signing-key-gone: not on this machine"

  Scenario: A project the token can't see backs up nothing
    Given a repository that declares its corpus key as "pub-signing-key-demo" in the project "elsewhere"
    When I back the keys up, giving the token "tok"
    Then it fails
    And it prints "this token sees 0 projects named elsewhere"
    And the SDK wrote nothing
