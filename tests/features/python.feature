Feature: Every Python package runs its suite by the same make targets
  A Python package's Makefile includes pubkit-python.mk, which pubkit
  manages: its venv, functional, lock, sync-check, wheel, clean and help.
  The suite runs on every core (pytest-xdist) and its coverage, the
  workers' and the programs' it starts, is combined into
  coverage/coverage.xml. sync --check holds every package to it.

  Scenario: sync writes pubkit-python.mk and seeds a Makefile that includes it
    Given a Python package
    When I run pubkit "sync"
    Then it succeeds
    And the repository has "pubkit-python.mk", "Makefile"
    And the Makefile includes pubkit-python.mk
    When I run pubkit "sync" "--check"
    Then it prints "in step with pubkit"

  Scenario: The suite runs on every core, its coverage combined, unless PYTEST_ARGS says otherwise
    Given a Python package
    When I run pubkit "sync"
    Then "make functional" runs pytest with "-n auto" and writes coverage/coverage.xml
    And "make functional PYTEST_ARGS=" runs pytest without "-n"
    And "make functional FUNCTIONAL_ENV=PYTHONPATH=src" runs the suite with "PYTHONPATH=src"
    And "make help" lists "venv, functional, lock, sync-check, wheel, clean, check"

  Scenario: A Makefile of its own that does not include pubkit-python.mk fails the check
    Given a Python package
    And the file "Makefile" reads "check:"
    When I run pubkit "sync"
    And I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "the Makefile does not include pubkit-python.mk"

  Scenario Outline: Coverage settings that would lose the workers' coverage fail the check
    Given a Python package
    And its pyproject.toml's [tool.coverage.run] is '<run>'
    When I run pubkit "sync"
    And I run pubkit "sync" "--check"
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
    When I run pubkit "sync"
    And I run pubkit "sync" "--check"
    Then it prints "in step with pubkit"

  Scenario Outline: Only a Python package gets pubkit-python.mk
    Given a <kind> package
    When I run pubkit "sync"
    Then the repository has no "pubkit-python.mk"

    Examples:
      | kind |
      | Node |
      | Rust |
