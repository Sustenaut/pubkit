Feature: Every Python package runs its suite by the same make targets
  A Python package's Makefile includes pubrules-python.mk, which pubrules
  manages: its venv, functional, lock, sync-check, wheel, clean and help.
  The suite runs on every core (pytest-xdist) and its coverage, the
  workers' and the programs' it starts, is combined into
  coverage/coverage.xml. sync --check holds every package to it.

  Scenario: sync writes pubrules-python.mk and seeds a Makefile that includes it
    Given a Python package
    When I run pubrules "sync"
    Then it succeeds
    And the repository has "pubrules-python.mk", "Makefile"
    And the Makefile includes pubrules-python.mk
    When I run pubrules "sync" "--check"
    Then it prints "in step with pubrules"

  Scenario: The suite runs on every core, its coverage combined, unless PYTEST_ARGS says otherwise
    Given a Python package
    When I run pubrules "sync"
    Then "make functional" runs pytest with "-n auto" and writes coverage/coverage.xml
    And "make functional PYTEST_ARGS=" runs pytest without "-n"
    And "make functional FUNCTIONAL_ENV=PYTHONPATH=src" runs the suite with "PYTHONPATH=src"
    And "make help" lists "venv, functional, lock, sync-check, wheel, clean, check"

  Scenario: A Makefile of its own that does not include pubrules-python.mk fails the check
    Given a Python package
    And a file "Makefile" holding "check:"
    When I run pubrules "sync"
    And I run pubrules "sync" "--check"
    Then it fails
    And stderr mentions "the Makefile does not include pubrules-python.mk"

  Scenario Outline: Coverage settings that would lose the workers' coverage fail the check
    Given a Python package
    And its pyproject.toml's [tool.coverage.run] is '<run>'
    When I run pubrules "sync"
    And I run pubrules "sync" "--check"
    Then it fails
    And stderr mentions "[tool.coverage.run] must set patch"

    Examples:
      | run                                      |
      | parallel = true                          |
      | patch = ["subprocess"]                   |
      | patch = ["execv"]\nparallel = true       |

  Scenario: Coverage settings that combine every worker's pass
    Given a Python package
    And its pyproject.toml's [tool.coverage.run] is 'patch = ["subprocess", "execv"]\nparallel = true'
    When I run pubrules "sync"
    And I run pubrules "sync" "--check"
    Then it prints "in step with pubrules"

  Scenario Outline: Only a Python package gets pubrules-python.mk
    Given a <kind> package
    When I run pubrules "sync"
    Then the file "pubrules-python.mk" does not exist

    Examples:
      | kind |
      | Node |
      | Rust |
