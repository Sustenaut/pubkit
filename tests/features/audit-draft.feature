Feature: pubrules audit draft has Claude draft the rules left for review
  `pubrules audit draft --package NAME` sends the Claude API the diff since the
  last release and the judged rules the report still marks for review, asks
  for structured JSON, and merges each verdict into the report marked as
  machine-drafted, for a person to review. Here the API is a fake on this
  machine, so nothing reaches the network.

  Background:
    Given the crate "library" released as "v0.1.0" and changed since, with pubrel giving "0.1.1"
    And the audit of "demo" has been prepared
    And a fake Claude API that drafts every rule it is sent

  Scenario: Each rule left for review is drafted, and marked for a person
    When I run pubrules "audit" "draft" "--package" "demo"
    Then it succeeds
    And "audit/demo-0.1.1.md" contains "**[machine-drafted by claude-opus-5-5: needs human review]** Verdict: falls short."
    And "audit/demo-0.1.1.md" contains "- **blocks** (`src/lib.rs:1`): drafted for §1.1"
    And "audit/demo-0.1.1.md" does not contain "[needs review]"
    And the report "demo" "0.1.1" has the header "auditor: pubrules audit prepare (pubrules"
    And the report's blocking count rose by the drafted findings that block
    When I run pubrules "audit" "--check" "demo" "0.1.1"
    Then it fails
    And stderr mentions "marked [machine-drafted by claude-opus-5-5: needs human review]"

  Scenario: The request is structured output, with the judged rules and the diff only
    When I run pubrules "audit" "draft" "--package" "demo"
    Then it succeeds
    And the API was asked by model "claude-opus-5-5" with the key "test-key"
    And the API was asked for JSON matching the draft schema
    And the API was sent the rule "2.2" and not the checked rule "RS-DOC-04"
    And the API was sent the diff since "v0.1.0"

  Scenario Outline: The model can be chosen
    Given <how>
    When I run pubrules <command>
    Then it succeeds
    And the API was asked by model "<model>" with the key "test-key"

    Examples:
      | how                                       | command                                                         | model             |
      | PUBRULES_AUDIT_MODEL is "claude-sonnet-5-5" | "audit" "draft" "--package" "demo"                              | claude-sonnet-5-5 |
      | PUBRULES_AUDIT_MODEL is "claude-sonnet-5-5" | "audit" "draft" "--package" "demo" "--model" "claude-haiku-5-5" | claude-haiku-5-5  |

  Scenario: Without a key, nothing is sent
    Given no ANTHROPIC_API_KEY
    When I run pubrules "audit" "draft" "--package" "demo"
    Then it fails
    And stderr mentions "needs ANTHROPIC_API_KEY: run it through with-secrets"
    And the API was asked nothing

  Scenario: A refusal leaves the report as it was
    Given the fake Claude API declines
    When I run pubrules "audit" "draft" "--package" "demo"
    Then it fails
    And stderr mentions "the model declined"
    And "audit/demo-0.1.1.md" contains "[needs review]"

  Scenario: An error from the API is reported
    Given the fake Claude API answers 401 "invalid x-api-key"
    When I run pubrules "audit" "draft" "--package" "demo"
    Then it fails
    And stderr mentions "the Claude API refused the request (401): invalid x-api-key"

  Scenario: A report with nothing left to review has nothing to draft
    Given a person reviews the report "demo" "0.1.1" and resolves what blocks
    When I run pubrules "audit" "draft" "--package" "demo"
    Then it succeeds
    And it prints "nothing to draft"
    And the API was asked nothing

  Scenario: A report that marks a rule the standard does not have is refused
    Given the report "demo" "0.1.1" marks the rule "9.9" for review, which the standard does not have
    When I run pubrules "audit" "draft" "--package" "demo"
    Then it fails
    And stderr mentions "marks rules 9.9, which this pubrules' standard does not have: prepare the report again"
    And the API was asked nothing
