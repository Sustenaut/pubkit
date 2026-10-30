Feature: A repos file says which repositories pubkit acts on, and a state file which an owner has
  --repos FILE takes the place of protected.json and governed.json, which stay
  for one release with a warning. How a repository is protected stays in its
  own pubkit.json. `pubkit state --check` is a state repository's fast check.

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

  Scenario: --repos takes the place of the other lists
    Given the repos file marks "example-org/one" to protect, and "example-org/two" not
    When I run pubkit "protect" "--repos" "repos.json" "--config" "repos.json"
    Then it does not succeed
    And it warns "--repos takes the place of --config and --require"

  Scenario: protected.json still works, with a warning
    Given protected.json lists "example-org/one"
    When I run pubkit "protect" "--check"
    Then it prints "example-org/one: no ruleset on its default branch"
    And it warns "go in a later release"

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

  Scenario: The board grades what the repos file names, by each one's roles
    Given the build recording, and a repos file with projects
    When I run pubkit "board" "--out" "site" "--replay" "recorded.json" "--repos" "repos.json"
    Then it succeeds
    And it warns of nothing
    And "site/board.json" lists "example-org/pubthing" in the project "pub"
    And "site/board.json" says "example-org/site-thing" has no protection to differ
    And "site/board.json" says "example-org/pubthing" is governed
    And "site/board.json" names its calls under the connector "repos"
    And "site/index.html" shows "example-org/pubthing" in the project "pub"

  Scenario: The board's old lists still work, with a warning
    Given the build recording, protected.json and governed.json in the repository
    When I run pubkit "board" "--out" "site" "--replay" "recorded.json"
    Then it succeeds
    And it warns "go in a later release"

  Scenario: The board takes one or the other
    Given the build recording, and a repos file with projects
    When I run pubkit "board" "--out" "site" "--repos" "repos.json" "--governed" "repos.json"
    Then it does not succeed
    And it warns "--repos takes the place of --config and --governed"

  Scenario: A repos file the board can't read is reported
    Given the file "repos.json" reads "nonsense"
    When I run pubkit "board" "--out" "site" "--replay" "repos.json" "--repos" "repos.json"
    Then it does not succeed

  Scenario: pubkit freshness reports what the file governs, checked out or not
    Given the file "repos.json" reads "{"repositories": [{"repo": "o/here", "govern": true}, {"repo": "o/gone", "govern": true}, {"repo": "o/other"}]}"
    And the file "here/tests/requirements.lock" reads "pubkit @ https://x/download/v0.1.0/pubkit-0.1.0-py3-none-any.whl"
    When I run pubkit "freshness" "--repos" "repos.json" "."
    Then it succeeds
    And it prints "| here | 0.1.0 |"
    And it prints "| gone | - |"
    And it prints "not checked out"
    And it does not print "other"

  Scenario: pubkit freshness needs something to report on
    When I run pubkit "freshness"
    Then it does not succeed
    And it warns "name the checkouts, or pass --repos FILE"

  Scenario: --repos takes one directory
    Given the file "repos.json" reads "{"repositories": []}"
    When I run pubkit "freshness" "--repos" "repos.json" "a" "b"
    Then it does not succeed
    And it warns "--repos takes one directory"

  Scenario: board.yml takes the repos file as an input
    When I parse the workflow "board.yml"
    Then it passes the "repos" input to pubkit board as a file, and otherwise the old lists

  Scenario: A sound state file passes
    Given the state of "example-org" has "one" with protect on and "two" with protect off
    And "example-org/one" has a protect block requiring "ci / passed"
    And "example-org/two" exists
    When I run pubkit "state" "--check"
    Then it succeeds
    And it prints "2 repositories of example-org, all sound"

  Scenario: A state file names a repository that isn't there
    Given the state of "example-org" has "one" with protect off and "two" with protect off
    And "example-org/one" exists
    When I run pubkit "state" "--check"
    Then it does not succeed
    And it prints "example-org/two does not exist"

  Scenario: A state file names a repository that has moved
    Given the state of "example-org" has "one" with protect off and "two" with protect off
    And "example-org/one" exists
    And "example-org/two" is now "other-org/two"
    When I run pubkit "state" "--check"
    Then it does not succeed
    And it prints "example-org/two is now other-org/two"

  Scenario: A repository to protect needs a protect block
    Given the state of "example-org" has "one" with protect on and "two" with protect off
    And "example-org/one" exists
    And "example-org/one" has the pubkit.json "{"kind": "utility"}"
    And "example-org/two" exists
    When I run pubkit "state" "--check"
    Then it does not succeed
    And it prints "example-org/one has "protect" on, but its pubkit.json has no protect block"

  Scenario: GitHub refusing is not taken for a missing repository
    Given the state of "example-org" has "one" with protect off and "two" with protect off
    And "example-org/one" exists
    And GitHub refuses "example-org/two"
    When I run pubkit "state" "--check"
    Then it does not succeed
    And it prints "example-org/two: gh api"

  Scenario: A state file may name a different path
    Given the file "other.json" reads "{"owner": "example-org", "repos": {}}"
    When I run pubkit "state" "--check" "other.json"
    Then it succeeds
    And it prints "0 repositories of example-org"

  Scenario Outline: A state file of the wrong shape is refused, without asking GitHub
    Given the file "state.json" reads "<text>"
    When I run pubkit "state" "--check"
    Then it does not succeed
    And it prints "<message>"
    And GitHub was asked nothing

    Examples:
      | text                                                                                           | message                          |
      | []                                                                                             | not an object                    |
      | {"repos": {}}                                                                                  | "owner" must be a GitHub owner   |
      | {"owner": 1, "repos": {}}                                                                      | "owner" must be a GitHub owner   |
      | {"owner": "o"}                                                                                 | "repos" must be an object        |
      | {"owner": "o", "repos": {}, "extra": 1}                                                        | extra is not a setting           |
      | {"owner": "o", "repos": {"a/b": {"board": true, "protect": true, "govern": true}}}             | a/b is not a repository's name   |
      | {"owner": "o", "repos": {"a": 1}}                                                              | its roles must be an object      |
      | {"owner": "o", "repos": {"a": {"board": true, "protect": true}}}                               | "govern" must be true or false   |
      | {"owner": "o", "repos": {"a": {"board": 1, "protect": true, "govern": true}}}                  | "board" must be true or false    |
      | {"owner": "o", "repos": {"a": {"board": true, "protect": true, "govern": true, "x": 1}}}       | x is not a setting               |
      | {"owner": "o", "repos": {"a": {"board": true, "protect": true, "govern": true, "project": 1}}} | "project" must be a name         |

  Scenario: A state file that can't be read says so
    Given the file "state.json" reads "nonsense"
    When I run pubkit "state" "--check"
    Then it does not succeed
    And it warns "state.json: Expecting value"

  Scenario: state takes only --check
    When I run pubkit "state"
    Then it does not succeed
