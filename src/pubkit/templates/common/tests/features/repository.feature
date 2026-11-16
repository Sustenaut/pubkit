Feature: The repository
  Seeded by `pubkit init`, so the suite is Gherkin from the first commit
  (`pubkit gherkin`): every test here is a scenario. Replace this one with
  the repository's own behaviour.

  Scenario: pubkit.json says what kind of repository this is
    Given the repository's pubkit.json
    Then it names the repository's kind
