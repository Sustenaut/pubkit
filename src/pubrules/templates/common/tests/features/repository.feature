Feature: The repository
  Seeded by `pubrules init`, so the suite is Gherkin from the first commit
  (`pubrules gherkin`): every test here is a scenario. Replace this one with
  the repository's own behaviour.

  Scenario: pubrules.json says what kind of repository this is
    Given the repository's pubrules.json
    Then it names the repository's kind
