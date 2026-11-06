Feature: pubkit variables keeps repositories' GitHub variables to a state file
  A repository's pubkit.json declares the names of the variables it reads; the owner's
  state file gives their values, strings or JSON. --check prints how GitHub differs and
  fails; --apply sets what differs and runs the repository's deploy workflow once.

  Scenario: Variables GitHub holds as the state gives them pass, however their JSON is spaced
    Given "example-org/one" declares "SOME_MAP" and "SOME_URL", deploying with "deploy.yml"
    And the state gives "one" the variables "{"SOME_MAP": {"a": "x", "b": "y"}, "SOME_URL": "https://example.com"}"
    And GitHub holds "SOME_MAP" in "example-org/one" as "{ "b": "y",  "a": "x" }"
    And GitHub holds "SOME_URL" in "example-org/one" as "https://example.com"
    When I run pubkit "variables" "--check"
    Then it succeeds
    And it prints "example-org/one: 2 variables as the state says"

  Scenario: --check shows the JSON entry that changed, and sets nothing
    Given "example-org/one" declares "SOME_MAP" and "SOME_URL", deploying with "deploy.yml"
    And the state gives "one" the variables "{"SOME_MAP": {"a": "x", "b": "z"}, "SOME_URL": "https://example.com"}"
    And GitHub holds "SOME_MAP" in "example-org/one" as "{"a":"x","b":"y"}"
    And GitHub holds "SOME_URL" in "example-org/one" as "https://example.com"
    When I run pubkit "variables" "--check"
    Then it does not succeed
    And it prints "example-org/one: 1 of 2 variables differ from the state"
    And it prints "-  "b": "y""
    And it prints "+  "b": "z""
    And it does not print ""a": "x""
    And nothing was set or run

  Scenario: --apply sets only what differs, compact, and runs the deploy workflow once
    Given "example-org/one" declares "SOME_MAP" and "SOME_URL", deploying with "deploy.yml"
    And the state gives "one" the variables "{"SOME_MAP": {"b": "z", "a": "x"}, "SOME_URL": "https://new.example.com"}"
    And GitHub holds "SOME_MAP" in "example-org/one" as "{"a":"x","b":"y"}"
    And GitHub holds "SOME_URL" in "example-org/one" as "https://example.com"
    When I run pubkit "variables" "--apply"
    Then it succeeds
    And GitHub holds "SOME_MAP" in "example-org/one" as "{"b":"z","a":"x"}" now
    And GitHub holds "SOME_URL" in "example-org/one" as "https://new.example.com" now
    And "deploy.yml" ran on "main" in "example-org/one" 1 time
    And no value was on a command line

  Scenario: --apply with nothing to change sets and runs nothing
    Given "example-org/one" declares "SOME_URL" and "SOME_MAP", deploying with "deploy.yml"
    And the state gives "one" the variables "{"SOME_URL": "https://example.com"}"
    And GitHub holds "SOME_URL" in "example-org/one" as "https://example.com"
    When I run pubkit "variables" "--apply"
    Then it succeeds
    And nothing was set or run

  Scenario: A variable GitHub doesn't hold yet is set
    Given "example-org/one" declares "SOME_URL" and "SOME_MAP", without a deploy workflow
    And the state gives "one" the variables "{"SOME_URL": "https://example.com"}"
    When I run pubkit "variables" "--check"
    Then it does not succeed
    And it prints "SOME_URL is not set; the state has:"
    When I run pubkit "variables" "--apply"
    Then it succeeds
    And GitHub holds "SOME_URL" in "example-org/one" as "https://example.com" now

  Scenario: A variable its pubkit.json doesn't declare is never set
    Given "example-org/one" declares "SOME_URL" and "SOME_MAP", deploying with "deploy.yml"
    And the state gives "one" the variables "{"OTHER": "x"}"
    When I run pubkit "variables" "--apply"
    Then the exit code is 1
    And it prints "example-org/one: its pubkit.json doesn't declare OTHER"
    And nothing was set or run

  Scenario: A repository the state gives no variables is left alone, unless named
    Given "example-org/one" declares "SOME_URL" and "SOME_MAP", deploying with "deploy.yml"
    And the state gives "one" the variables "{"SOME_URL": "https://example.com"}"
    And GitHub holds "SOME_URL" in "example-org/one" as "https://example.com"
    When I run pubkit "variables" "--check"
    Then it succeeds
    And it does not print "example-org/two"
    When I run pubkit "variables" "--check" "two"
    Then the exit code is 1
    And it warns "the state gives two no variables"

  Scenario: A repository named in another owner is refused
    Given "example-org/one" declares "SOME_URL" and "SOME_MAP", deploying with "deploy.yml"
    And the state gives "one" the variables "{"SOME_URL": "https://example.com"}"
    When I run pubkit "variables" "--check" "other-org/one"
    Then the exit code is 1
    And it warns "other-org/one is not one of example-org's"

  Scenario: Variables that can't be read fail, and say so
    Given "example-org/one" declares "SOME_URL" and "SOME_MAP", deploying with "deploy.yml"
    And the state gives "one" the variables "{"SOME_URL": "https://example.com"}"
    And GitHub refuses to show the variables of "example-org/one"
    When I run pubkit "variables" "--check"
    Then the exit code is 1
    And it prints "example-org/one: its variables can't be read"

  Scenario: variables takes --check or --apply
    When I run pubkit "variables"
    Then the exit code is 2

  Scenario: The state check passes variables the repository declares
    Given "example-org/one" declares "SOME_URL" and "SOME_MAP", deploying with "deploy.yml"
    And the state gives "one" the variables "{"SOME_URL": "https://example.com", "SOME_MAP": {"a": 1}}"
    When I run pubkit "state" "--check"
    Then it succeeds

  Scenario: The state check refuses a variable the repository doesn't declare
    Given "example-org/one" declares "SOME_URL" and "SOME_MAP", deploying with "deploy.yml"
    And the state gives "one" the variables "{"SOME_URL": "https://example.com", "OTHER": "x"}"
    When I run pubkit "state" "--check"
    Then it does not succeed
    And it prints "example-org/one: its pubkit.json doesn't declare OTHER, which the state gives it"

  Scenario: The state check refuses variables for a repository that declares none
    Given "example-org/one" has the pubkit.json "{"kind": "utility"}"
    And the state gives "one" the variables "{"SOME_URL": "https://example.com"}"
    When I run pubkit "state" "--check"
    Then it does not succeed
    And it prints "example-org/one has variables in the state, but its pubkit.json declares no variables"

  Scenario Outline: Variables of the wrong shape are refused, without asking GitHub
    Given the state gives "one" the variables "<variables>"
    When I run pubkit "state" "--check"
    Then it does not succeed
    And it prints "<message>"

    Examples:
      | variables                 | message                                  |
      | []                        | its variables must be an object          |
      | {"lower": "x"}            | "lower" is not a variable's name         |
      | {"GITHUB_TOKEN": "x"}     | "GITHUB_TOKEN" is not a variable's name  |
      | {"SOME": ""}              | SOME has no value                        |
      | {"SOME": null}            | SOME has no value                        |

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
