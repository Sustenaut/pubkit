Feature: Every repository's main branch carries pubkit's one ruleset
  src/pubkit/ruleset.json is the ruleset; src/pubkit/protected.json lists
  the repositories and the checks each requires. Both ship in the wheel. `pubkit protect` creates or
  updates the ruleset through the GitHub API, and `pubkit protect --check`
  reports how a repository differs, changing nothing.

  Scenario: The ruleset keeps main to pull requests that passed their checks
    When I build the ruleset requiring "ci / passed, SonarCloud Code Analysis"
    Then it targets the default branch and nobody may bypass it
    And it blocks deleting and force-pushing the branch
    And it takes changes only by pull request, with no approval needed
    And it requires "ci / passed" from GitHub Actions and "SonarCloud Code Analysis" from SonarCloud

  Scenario: protected.json covers every governed repository
    When I read pubkit's protected.json
    Then every repository in it requires at least one check, and is listed once
    And every repository in governed.json is in it

  Scenario: protect creates the ruleset where there is none
    Given protected.json lists "schryer/foo" requiring "ci / passed, release / release"
    And GitHub has no ruleset in "schryer/foo"
    When I run pubkit "protect" "--config" "protected.json"
    Then it succeeds
    And it prints "schryer/foo: created"
    And GitHub was sent the ruleset for "schryer/foo" requiring "ci / passed, release / release"

  Scenario: protect --check reports a ruleset that differs, and changes nothing
    Given protected.json lists "schryer/foo" requiring "ci / passed"
    And GitHub has the ruleset "main" in "schryer/foo" requiring "test"
    When I run pubkit "protect" "--config" "protected.json" "--check"
    Then it fails
    And it prints "does not require ci / passed (app 15368)"
    And it prints "also requires test (app 15368)"
    And it prints "name is "main""
    And GitHub was sent nothing

  Scenario: protect updates the ruleset already on main, and renames it
    Given protected.json lists "schryer/foo" requiring "ci / passed"
    And GitHub has the ruleset "main" in "schryer/foo" requiring "test"
    When I run pubkit "protect" "--config" "protected.json" "schryer/foo"
    Then it succeeds
    And it prints "schryer/foo: updated "main""
    And GitHub was sent the ruleset for "schryer/foo" requiring "ci / passed", replacing "main"

  Scenario: A ruleset that matches is up to date, whatever GitHub adds to it
    Given protected.json lists "schryer/foo" requiring "ci / passed"
    And GitHub has pubkit's ruleset in "schryer/foo" requiring "ci / passed"
    When I run pubkit "protect" "--config" "protected.json" "--check"
    Then it succeeds
    And it prints "schryer/foo: up to date"

  Scenario: protect names the line to add for a repository protected.json lacks
    Given protected.json lists "schryer/foo" requiring "ci / passed"
    When I run pubkit "protect" "--config" "protected.json" "schryer/bar"
    Then it fails
    And stderr mentions "schryer/bar not in protected.json"

  Scenario: protect says who may change a ruleset when GitHub refuses
    Given protected.json lists "schryer/foo" requiring "ci / passed"
    And GitHub refuses to change rulesets in "schryer/foo"
    When I run pubkit "protect" "--config" "protected.json"
    Then it fails
    And it prints "needs a repository admin"

  Scenario: protect refuses an entry that is not a repository
    Given protected.json lists "schryer/foo/../bar" requiring "ci / passed"
    When I run pubkit "protect" "--config" "protected.json" "--check"
    Then it fails
    And stderr mentions "needs a repo, OWNER/NAME"

  Scenario: Without --config, protect reads the list this pubkit ships
    Given GitHub has pubkit's ruleset in "schryer/pubkit" requiring "ci / passed, release / release, SonarCloud Code Analysis"
    When I run pubkit "protect" "--check" "schryer/pubkit"
    Then it succeeds
    And it prints "schryer/pubkit: up to date"

  Scenario: protect shows why GitHub rejected a ruleset
    Given protected.json lists "schryer/foo" requiring "ci / passed, SonarCloud Code Analysis"
    And GitHub rejects the ruleset for "schryer/foo" because "Invalid rule 'required_status_checks': integration 12526 is not installed"
    When I run pubkit "protect" "--config" "protected.json"
    Then it fails
    And it prints "Validation Failed (HTTP 422); Invalid rule 'required_status_checks': integration 12526 is not installed"
    And it prints nothing about a repository admin
