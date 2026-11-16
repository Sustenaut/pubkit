Feature: Every test is a Gherkin scenario
  `pubkit gherkin` checks that a repository's tests are Gherkin scenarios,
  and `pubkit sync --check` runs it in every repository but a content one.
  TEST-01: there is a feature file with a scenario. TEST-02: no test runs
  outside a scenario. TEST-03: every feature file runs. A rule the
  repository cannot meet yet is declared as an exception in pubkit.json,
  with a reason and a date, as for the Rust crate standard's rules.

  Scenario: A Gherkin suite passes
    Given a utility whose suite is Gherkin
    When I run pubkit "gherkin"
    Then it succeeds
    And it prints "every test is a Gherkin scenario"

  Scenario: A repository with no feature file fails TEST-01
    Given a utility whose suite is Gherkin
    And the suite's feature file is removed
    When I run pubkit "gherkin"
    Then it fails
    And stderr mentions "TEST-01: there is no Gherkin suite"

  Scenario: A feature file with no scenario fails TEST-01
    Given a utility whose suite is Gherkin
    And the file "tests/features/empty.feature" reads "Feature: Nothing yet"
    When I run pubkit "gherkin"
    Then it fails
    And stderr mentions "TEST-01: tests/features/empty.feature has no scenario"

  Scenario Outline: A test outside a scenario fails TEST-02
    Given a utility whose suite is Gherkin
    And the file "<path>" holds <test>
    When I run pubkit "gherkin"
    Then it fails
    And stderr mentions "TEST-02: <message>"

    Examples:
      | path                       | test                        | message                                                          |
      | tests/steps/test_plain.py  | a plain test function       | tests/steps/test_plain.py:1: test_adds is a test function, not a scenario |
      | tests/steps/test_plain.py  | a test class                | tests/steps/test_plain.py:1: TestAdding is a test class          |
      | tools/checks.py            | a unittest test case        | tools/checks.py:2: Adding is a unittest.TestCase                 |
      | tests/steps/test_plain.py  | a binding that tests too    | tests/steps/test_plain.py:5: test_adds binds a scenario but tests in its body too |
      | worker/site.test.js        | a node:test test            | worker/site.test.js: a Node test outside a scenario              |
      | test/site.mjs              | a node:test test            | test/site.mjs: a Node test outside a scenario                    |
      | scripts/test-deploy.sh     | a shell script              | scripts/test-deploy.sh: a test script outside a scenario         |

  Scenario: Fixtures, steps and test helpers are not tests
    Given a utility whose suite is Gherkin
    And the file "tests/steps/test_plain.py" holds a fixture and a step
    And the file "test/fake-github.js" holds a fake server
    When I run pubkit "gherkin"
    Then it succeeds

  Scenario: A feature file nothing binds fails TEST-03
    Given a utility whose suite is Gherkin
    And the file "tests/features/unbound.feature" holds a scenario
    When I run pubkit "gherkin"
    Then it fails
    And stderr mentions "TEST-03: tests/features/unbound.feature: no scenarios() or @scenario names it"

  Scenario: A binding of the features directory runs every feature in it
    Given a utility whose suite is Gherkin
    And the file "tests/features/unbound.feature" holds a scenario
    And the suite binds the whole features directory
    When I run pubkit "gherkin"
    Then it succeeds

  Scenario: A step file may load node:test's mock beside cucumber-js
    Given a utility whose suite is Gherkin
    And the file "test/site.steps.js" holds cucumber steps that use node:test's mock
    When I run pubkit "gherkin"
    Then it succeeds

  Scenario: A package of the repository that runs quickpickle runs every feature file it is given
    Given a utility whose suite is Gherkin
    And the suite's feature file is removed
    And the file "widget/features/form.feature" holds a scenario
    And the file "widget/package.json" lists "quickpickle" as a dev dependency
    When I run pubkit "gherkin"
    Then it succeeds

  Scenario: cucumber-js runs every feature file it is given
    Given a utility whose suite is Gherkin
    And the suite's feature file is removed
    And the file "features/site.feature" holds a scenario
    And package.json lists "@cucumber/cucumber" as a dev dependency
    When I run pubkit "gherkin"
    Then it succeeds

  Scenario: A Rust crate's own tests are the Rust crate standard's
    Given a utility whose suite is Gherkin
    And the file "src/lib.rs" reads "#[test] fn adds() {}"
    When I run pubkit "gherkin"
    Then it succeeds

  Scenario: An exception waives a rule until its date, and says so
    Given a utility whose suite is Gherkin
    And the file "tests/steps/test_plain.py" holds a plain test function
    And pubkit.json declares an exception for "TEST-02" until "2026-12-31"
    When I run pubkit "gherkin"
    Then it succeeds
    And it prints "open exception: TEST-02 until 2026-12-31"

  Scenario: An expired exception fails
    Given a utility whose suite is Gherkin
    And pubkit.json declares an exception for "TEST-02" until "2026-10-01"
    When I run pubkit "gherkin"
    Then it fails
    And stderr mentions "TEST-02: the exception expired on 2026-10-01"

  Scenario: An exception for a rule that is not one fails
    Given a utility whose suite is Gherkin
    And pubkit.json declares an exception for "TEST-99" until "2026-12-31"
    When I run pubkit "gherkin"
    Then it fails
    And stderr mentions "TEST-99 is not a rule of the standard"

  Scenario: sync --check runs the Gherkin check
    Given a utility whose suite is Gherkin
    And the file "tests/steps/test_plain.py" holds a plain test function
    When I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "TEST-02: tests/steps/test_plain.py:1: test_adds is a test function"

  Scenario: A content repository is not held to it
    Given a repository initialised as "content"
    When I run pubkit "gherkin"
    Then it succeeds
    And it prints "the Gherkin rule does not apply to a content repository"

  Scenario: A new repository starts with a suite that passes
    Given a repository initialised as "utility"
    When I run pubkit "gherkin"
    Then it succeeds
    And the repository has "tests/features/repository.feature", "tests/steps/test_repository.py"
