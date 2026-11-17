Feature: The repository
  Seeded by `pubrules init`, so the suite is Gherkin from the first commit
  (`pubrules gherkin`): every test here is a scenario, set up by Givens, one
  action (its When) and checks of what came of it, on pubtest's steps
  (its GRAMMAR.md). Replace this one with the repository's
  own behaviour.

  Scenario: pubrules.json says what kind of repository this is
    When I read the repository's pubrules.json
    Then it is not refused
    And the result includes "kind"
