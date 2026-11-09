Feature: A repos file says which repositories pubkit protect acts on
  How a repository is protected stays in its own pubkit.json. pubfleet reads
  the same file, and makes it from an owner's state file.

  Scenario: pubkit protect --check reads the repositories the file marks
    Given the repos file marks "example-org/one" to protect, and "example-org/two" not
    And "example-org/one" has a protect block requiring "ci / passed"
    When I run pubkit "protect" "--check" "--repos" "repos.json"
    Then it prints "example-org/one: no ruleset on its default branch"
    And it does not print "example-org/two"
    And it warns of nothing

  Scenario: A repository marked to protect needs a protect block
    Given the repos file marks "example-org/one" to protect, and "example-org/two" not
    When I run pubkit "protect" "--check" "--repos" "repos.json"
    Then it does not succeed
    And it prints "example-org/one: no pubkit.json on its default branch"

  Scenario: --repos takes the place of --require
    Given the repos file marks "example-org/one" to protect, and "example-org/two" not
    When I run pubkit "protect" "--repos" "repos.json" "--require" "build"
    Then it does not succeed
    And it warns "--repos takes the place of --require"

  Scenario Outline: A repos file that is wrong is refused, saying why
    Given the file "repos.json" reads "<text>"
    When I run pubkit "protect" "--check" "--repos" "repos.json"
    Then it does not succeed
    And it warns "<message>"

    Examples:
      | text                                                       | message                     |
      | nonsense                                                   | needs a "repositories" list |
      | {"repositories": {}}                                       | is not a list               |
      | {"repositories": [{"protect": true}]}                      | needs a repo, OWNER/NAME    |
      | {"repositories": [{"repo": "nope"}]}                       | needs a repo, OWNER/NAME    |
      | {"repositories": [{"repo": "a/b", "checks": []}]}          | checks is not a setting     |
      | {"repositories": [{"repo": "a/b", "protect": "yes"}]}      | "protect" must be true      |
      | {"repositories": [{"repo": "a/b", "project": 1}]}          | "project" must be a name    |
      | {"repositories": [{"repo": "a/b"}, {"repo": "a/b"}]}       | a/b more than once          |
