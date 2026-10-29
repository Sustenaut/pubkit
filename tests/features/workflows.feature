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
      | workflow                  |
      | rust-ci.yml               |
      | python-ci.yml             |
      | service-ci.yml            |
      | node-ci.yml               |
      | job-node-test.yml         |
      | job-node-browser.yml      |
      | job-node-audit.yml        |
      | job-python-functional.yml |
      | job-python-browser.yml    |
      | job-python-audit.yml      |
      | job-sonar.yml             |
      | job-rust-gates.yml        |
      | job-rust-test.yml         |
      | job-rust-functional.yml   |
      | job-rust-fuzz.yml         |
      | job-rust-cross.yml        |
      | job-rust-audit.yml        |
      | job-rust-toolchains.yml   |
      | job-service-checks.yml    |
      | job-service-terraform.yml |
      | job-service-tests.yml     |
      | board.yml                 |

  Scenario: board.yml grades with the caller's token, keeps board.json and deploys nothing
    When I parse the workflow "board.yml"
    Then it takes a required "token" secret and reads the repository's contents only
    And it runs pubkit board with the token as GITHUB_TOKEN
    And it uploads board.json as the "board" artifact, kept 90 days, and nothing else runs

  Scenario Outline: A reusable workflow ends in the one check a ruleset requires
    When I parse the workflow "<workflow>"
    Then its "passed" job always runs and needs every other job

    Examples:
      | workflow      |
      | rust-ci.yml   |
      | python-ci.yml |
      | service-ci.yml |
      | node-ci.yml   |

  Scenario: rust-ci runs the targets pubkit required plans, in every job
    When I parse the workflow "rust-ci.yml"
    Then every job pubkit required plans for reads its targets from the plan job
    And the gates and functional inputs only add targets

  Scenario: rust-ci's plan hands the event, the branch and the changed files to pubkit
    When I parse the workflow "rust-ci.yml"
    Then the plan job compares a pull request with its base and passes it to pubkit required

  Scenario: The sonar job's layout comes before sonar-args, so a repository's own properties win
    When I parse the workflow "job-sonar.yml"
    Then the scan's layout comes before sonar-args and pubkit.json's sonar args

  Scenario Outline: A package's reusable workflow sends its code to SonarQube Cloud when given a token
    When I parse the workflow "<workflow>"
    Then it takes an optional SONAR_TOKEN secret
    And its "sonar" job runs the pinned SonarQube scan only when the token is set
    And the scan names the project OWNER_REPO in the organization owner, lower-cased unless pubkit.json names it
    And without a token it warns, unless the pull request comes from a fork
    And the scan waits for the quality gate, so a failing gate fails the job

    Examples:
      | workflow      |
      | rust-ci.yml   |
      | python-ci.yml |
      | node-ci.yml   |
      | job-sonar.yml |

  Scenario Outline: A package's reusable workflow gives SonarQube Cloud the tests' coverage
    When I parse the workflow "<workflow>"
    Then the "<job>" job hands its coverage report to the sonar scan as "<property>"

    Examples:
      | workflow      | job        | property                          |
      | python-ci.yml | functional | sonar.python.coverage.reportPaths |
      | rust-ci.yml   | functional | sonar.rust.lcov.reportPaths       |
      | rust-ci.yml   | functional | sonar.python.coverage.reportPaths |
      | node-ci.yml   | test       | sonar.javascript.lcov.reportPaths |
      | python-ci.yml | browser    | sonar.python.coverage.reportPaths |
      | node-ci.yml   | browser    | sonar.javascript.lcov.reportPaths |

  Scenario Outline: A package's reusable workflow scans only once the browser tests' coverage is in
    When I parse the workflow "<workflow>"
    Then the sonar job waits for the browser job, and runs when it was skipped

    Examples:
      | workflow      |
      | python-ci.yml |
      | node-ci.yml   |

  Scenario: rust-ci measures coverage with the functional targets only when there is a scan to send it to
    When I parse the workflow "rust-ci.yml"
    Then the functional job runs make coverage in place of make functional when given a SONAR_TOKEN
    And the token itself never reaches the functional job

  Scenario Outline: Every action a workflow uses is pinned to a commit
    When I parse the workflow "<workflow>"
    Then every action it uses is pinned to a full commit hash, with its version in a comment

    Examples:
      | workflow       |
      | ci.yml         |
      | nightly.yml    |
      | rust-ci.yml    |
      | python-ci.yml  |
      | service-ci.yml |
      | node-ci.yml    |
      | job-node-test.yml         |
      | job-node-browser.yml      |
      | job-node-audit.yml        |
      | job-python-functional.yml |
      | job-python-browser.yml    |
      | job-python-audit.yml      |
      | job-osv.yml               |
      | job-review.yml            |
      | job-sonar.yml             |
      | job-rust-gates.yml        |
      | job-rust-test.yml         |
      | job-rust-functional.yml   |
      | job-rust-msrv.yml         |
      | job-rust-fuzz.yml         |
      | job-rust-cross.yml        |
      | job-rust-audit.yml        |
      | job-rust-toolchains.yml   |
      | job-service-checks.yml    |
      | job-service-terraform.yml |
      | job-service-tests.yml     |

  Scenario: node-ci installs dependencies without running their scripts
    When I parse the workflow "node-ci.yml"
    Then its setup input runs npm ci with --ignore-scripts
    And its browser-setup input runs npx with --no-install

  Scenario Outline: A package's reusable workflow checks advisories on every run, with no switch
    When I parse the workflow "<workflow>"
    Then its "audit" job runs "<check>" on every event
    And no input turns the audit job off

    Examples:
      | workflow      | check                                      |
      | node-ci.yml   | npm audit --package-lock-only              |
      | python-ci.yml | pypa/gh-action-pip-audit                   |

  Scenario: rust-ci fuzzes long on a schedule unless pubkit.json turns the long run off
    When I parse the workflow "rust-ci.yml"
    Then its fuzz job reads security.fuzz.rust from pubkit.json on a scheduled run

  Scenario Outline: A package's reusable workflow tries more property-test inputs on a schedule unless pubkit.json says not to
    When I parse the workflow "<workflow>"
    Then its "<job>" job sets PUBKIT_FUZZ_RUNS before its tests, from security.fuzz.<language> on a scheduled run

    Examples:
      | workflow      | job        | language |
      | python-ci.yml | functional | python   |
      | node-ci.yml   | test       | node     |

  Scenario Outline: A package's reusable workflow scans every lockfile with OSV-Scanner and reviews new dependencies
    When I parse the workflow "<workflow>"
    Then its "osv" job runs "google/osv-scanner-action" on every event
    And its "review" job runs dependency review on public pull requests only
    And its passed job lets the review job be skipped

    Examples:
      | workflow      |
      | rust-ci.yml   |
      | node-ci.yml   |
      | python-ci.yml |
