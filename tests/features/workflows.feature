Feature: The reusable workflows parse as GitHub reads them
  pubkit's own CI runs only python-ci.yml, so a malformed rust-ci.yml would
  first fail in someone else's repository. Every workflow it ships is parsed
  here instead.

  Scenario Outline: A reusable workflow declares well-formed inputs
    When I parse the workflow "<workflow>"
    Then it is called with workflow_call
    And every input has only a type, a default, a description or required
    And every job the workflow runs uses only its declared inputs

    Examples:
      | workflow      |
      | rust-ci.yml   |
      | python-ci.yml |

  Scenario Outline: A reusable workflow ends in the one check a ruleset requires
    When I parse the workflow "<workflow>"
    Then its "passed" job always runs and needs every other job

    Examples:
      | workflow      |
      | rust-ci.yml   |
      | python-ci.yml |

  Scenario: rust-ci runs the targets pubkit required plans, in every job
    When I parse the workflow "rust-ci.yml"
    Then every job pubkit required plans for reads its targets from the plan job
    And the gates and functional inputs only add targets

  Scenario: rust-ci's plan hands the event, the branch and the changed files to pubkit
    When I parse the workflow "rust-ci.yml"
    Then the plan job compares a pull request with its base and passes it to pubkit required
