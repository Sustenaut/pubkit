Feature: A repository's pubkit.json names the GitHub variables it reads, in a block sync checks
  The values are in the owner's state file, which pubfleet's variables command
  keeps GitHub to; pubkit checks only the block's shape.

  Scenario Outline: sync refuses a variables block of the wrong shape
    Given a repository initialised as "utility"
    And its pubkit.json's variables block is "<block>"
    When I run pubkit "sync" "--check"
    Then the exit code is 1
    And it warns "<message>"

    Examples:
      | block                                        | message                              |
      | ["SOME"]                                     | it must be an object                 |
      | {}                                           | "names" must list at least one       |
      | {"names": ["SOME", "SOME"]}                  | lists a variable twice               |
      | {"names": ["some"]}                          | "some" is not a variable's name      |
      | {"names": ["SOME"], "deploy": "../x.yml"}    | "deploy" must be a workflow's file   |
      | {"names": ["SOME"], "other": 1}              | other is not a setting               |
