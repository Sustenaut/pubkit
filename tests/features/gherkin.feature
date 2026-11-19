Feature: Every test is a Gherkin scenario
  `pubrules gherkin` checks that a repository's tests are Gherkin scenarios,
  and `pubrules sync --check` runs it in every repository but a content one.
  TEST-01: there is a feature file with a scenario. TEST-02: no test runs
  outside a scenario. TEST-03: every feature file runs. TEST-04: every
  scenario has a When of its own, the one action it tests. A rule the
  repository cannot meet yet is declared as an exception in pubrules.json,
  with a reason and a date, as for the Rust crate standard's rules.

  Scenario: A Gherkin suite passes
    Given a utility whose suite is Gherkin
    When I run pubrules "gherkin"
    Then it succeeds
    And it prints "every test is a Gherkin scenario"

  Scenario: A repository with no feature file fails TEST-01
    Given a utility whose suite is Gherkin
    And the suite's feature file is removed
    When I run pubrules "gherkin"
    Then it fails
    And stderr mentions "TEST-01: there is no Gherkin suite"

  Scenario: A feature file with no scenario fails TEST-01
    Given a utility whose suite is Gherkin
    And a file "tests/features/empty.feature" holding "Feature: Nothing yet"
    When I run pubrules "gherkin"
    Then it fails
    And stderr mentions "TEST-01: tests/features/empty.feature has no scenario"

  Scenario Outline: A test outside a scenario fails TEST-02
    Given a utility whose suite is Gherkin
    And the file "<path>" holds <test>
    When I run pubrules "gherkin"
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
      | tests/deploy.test.sh       | a shell script              | tests/deploy.test.sh: a test script outside a scenario           |

  Scenario: Fixtures, steps and test helpers are not tests
    Given a utility whose suite is Gherkin
    And the file "tests/steps/test_plain.py" holds a fixture and a step
    And the file "test/fake-github.js" holds a fake server
    When I run pubrules "gherkin"
    Then it succeeds

  Scenario: A feature file nothing binds fails TEST-03
    Given a utility whose suite is Gherkin
    And the file "tests/features/unbound.feature" holds a scenario
    When I run pubrules "gherkin"
    Then it fails
    And stderr mentions "TEST-03: tests/features/unbound.feature: no scenarios() or @scenario names it"

  Scenario: A binding of the features directory runs every feature in it
    Given a utility whose suite is Gherkin
    And the file "tests/features/unbound.feature" holds a scenario
    And the suite binds the whole features directory
    When I run pubrules "gherkin"
    Then it succeeds

  Scenario: A step file may load node:test's mock beside cucumber-js
    Given a utility whose suite is Gherkin
    And the file "test/site.steps.js" holds cucumber steps that use node:test's mock
    When I run pubrules "gherkin"
    Then it succeeds

  Scenario: A package of the repository that runs quickpickle runs every feature file it is given
    Given a utility whose suite is Gherkin
    And the suite's feature file is removed
    And the file "widget/features/form.feature" holds a scenario
    And the file "widget/package.json" lists "quickpickle" as a dev dependency
    When I run pubrules "gherkin"
    Then it succeeds

  Scenario: cucumber-js runs every feature file it is given
    Given a utility whose suite is Gherkin
    And the suite's feature file is removed
    And the file "features/site.feature" holds a scenario
    And package.json lists "@cucumber/cucumber" as a dev dependency
    When I run pubrules "gherkin"
    Then it succeeds

  Scenario Outline: A scenario with no When of its own fails TEST-04
    Given a utility whose suite is Gherkin
    And the suite binds the whole features directory
    And the file "tests/features/checks.feature" holds <scenario>
    When I run pubrules "gherkin"
    Then it fails
    And stderr mentions "TEST-04: tests/features/checks.feature:<line>: Scenario: <title> has no When"

    Examples:
      | scenario                                   | line | title            |
      | a scenario without a When                  | 2    | Only a check     |
      | a scenario whose When is in its Background | 4    | Set up elsewhere |
      | a scenario whose When is in a docstring    | 2    | A When in the text |

  Scenario: An outline with a When, its action chained with And, meets TEST-04
    Given a utility whose suite is Gherkin
    And the suite binds the whole features directory
    And the file "tests/features/checks.feature" holds an outline with a When, chained with And
    When I run pubrules "gherkin"
    Then it succeeds

  Scenario: The move from a pubrules before TEST-04 waives it, until the end of 2026, where it fails
    Given a utility whose suite is Gherkin
    And the suite's scenario has no When
    And a file "tests/requirements.lock" holding "pubrules @ https://github.com/Sustenaut/pubrules/releases/download/v0.43.0/pubrules-0.43.0-py3-none-any.whl"
    When I run pubrules "pins" "--no-lock"
    Then it succeeds
    And it prints "pubrules.json: TEST-04 waived until 2026-12-31, for the 1 scenario with no When"
    And the file "pubrules.json" includes ""until": "2026-12-31""
    And the file "pubrules.json" includes "had no When on the move from pubrules 0.43.0, before the rule"

  Scenario: The move from pubkit 0.41.1 waives TEST-04 where it fails
    Given a utility whose suite is Gherkin
    And the suite's scenario has no When
    And a file "tests/requirements.lock" holding "pubkit @ https://github.com/Sustenaut/pubkit/releases/download/v0.41.1/pubkit-0.41.1-py3-none-any.whl"
    When I run pubrules "pins" "--no-lock"
    Then it succeeds
    And it prints "pubrules.json: TEST-04 waived until 2026-12-31, for the 1 scenario with no When"
    And the file "pubrules.json" includes "had no When on the move from pubkit 0.41.1, before the rule"

  Scenario: Once waived by the move, TEST-04 is an open exception
    Given a utility whose suite is Gherkin
    And the suite's scenario has no When
    And PUBRULES_ANY_VERSION is set
    And a file "tests/requirements.lock" holding "pubrules @ https://github.com/Sustenaut/pubrules/releases/download/v0.43.0/pubrules-0.43.0-py3-none-any.whl"
    When I run pubrules "pins" "--no-lock"
    And I run pubrules "gherkin"
    Then it succeeds
    And it prints "open exception: TEST-04 until 2026-12-31"

  Scenario: The move from a pubrules before TEST-04 waives nothing where every scenario has a When
    Given a utility whose suite is Gherkin
    And a file "tests/requirements.lock" holding "pubrules @ https://github.com/Sustenaut/pubrules/releases/download/v0.43.0/pubrules-0.43.0-py3-none-any.whl"
    When I run pubrules "pins" "--no-lock"
    Then it succeeds
    And the file "pubrules.json" does not include "TEST-04"

  Scenario: The move waives TEST-04 no more after 2026
    Given a utility whose suite is Gherkin
    And the suite's scenario has no When
    And today is "2027-01-01"
    And a file "tests/requirements.lock" holding "pubrules @ https://github.com/Sustenaut/pubrules/releases/download/v0.43.0/pubrules-0.43.0-py3-none-any.whl"
    When I run pubrules "pins" "--no-lock"
    Then it succeeds
    And the file "pubrules.json" does not include "TEST-04"

  Scenario: A Rust crate's own tests are the Rust crate standard's
    Given a utility whose suite is Gherkin
    And a file "src/lib.rs" holding "#[test] fn adds() {}"
    When I run pubrules "gherkin"
    Then it succeeds

  Scenario: An exception waives a rule until its date, and says so
    Given a utility whose suite is Gherkin
    And the file "tests/steps/test_plain.py" holds a plain test function
    And pubrules.json declares an exception for "TEST-02" until "2026-12-31"
    When I run pubrules "gherkin"
    Then it succeeds
    And it prints "every test is a Gherkin scenario, but for 1 open exception"
    And it prints "open exception: TEST-02 until 2026-12-31"

  Scenario: An expired exception fails
    Given a utility whose suite is Gherkin
    And pubrules.json declares an exception for "TEST-02" until "2026-10-01"
    When I run pubrules "gherkin"
    Then it fails
    And stderr mentions "TEST-02: the exception expired on 2026-10-01"

  Scenario: An exception for a rule that is not one fails
    Given a utility whose suite is Gherkin
    And pubrules.json declares an exception for "TEST-99" until "2026-12-31"
    When I run pubrules "gherkin"
    Then it fails
    And stderr mentions "TEST-99 is not a rule of the standard"

  Scenario: sync --check runs the Gherkin check
    Given a utility whose suite is Gherkin
    And the file "tests/steps/test_plain.py" holds a plain test function
    When I run pubrules "sync" "--check"
    Then it fails
    And stderr mentions "TEST-02: tests/steps/test_plain.py:1: test_adds is a test function"

  Scenario: A content repository is not held to it
    Given a repository initialised as "content"
    When I run pubrules "gherkin"
    Then it succeeds
    And it prints "the Gherkin rule does not apply to a content repository"

  Scenario: A seeded template compiled by the installer is not a template
    Given pubrules' templates hold a compiled copy of a seeded .py file, as pip leaves one
    When I run pubrules "sync" "--check"
    Then it reads every template, with no traceback

  Scenario: A new repository starts with a suite that passes
    Given a repository initialised as "utility"
    When I run pubrules "gherkin"
    Then it succeeds
    And the repository has "tests/features/repository.feature", "tests/steps/test_repository.py"
