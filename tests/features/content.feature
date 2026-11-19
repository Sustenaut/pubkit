Feature: A content repository is one pubrules neither releases nor deploys
  A content repository holds authored information, documents or data, and is
  private. pubrules keeps its common files in step and writes its CI, but it has
  no release, no release tags and no secrets wrapper: whatever builds, checks
  or versions what it holds is its own tooling's, run as the ci tests job.

  Scenario: init writes the common files only, and needs no project
    Given an empty repository
    When I run pubrules "init" "--kind=content"
    Then it succeeds
    And the repository has "pubrules", "AGENTS.md", "tests/requirements-pubrules.txt", "tests/install-lock", "tests/requirements.txt", ".github/workflows/pr.yml", ".github/workflows/main.yml"
    And the file "with-secrets" does not exist
    And the file "with-secrets.json" does not exist
    And the file "pubsec" does not exist
    And the file "pubdeploy" does not exist
    And the file "pubrules.mk" does not exist
    And the file "pubrules-python.mk" does not exist
    And the file "rust-toolchain.toml" does not exist
    And the file "Makefile" does not exist
    And pubrules.json reads {"kind": "content", "ci": {}}

  Scenario: A repository just initialised as content is in step
    Given a repository initialised as "content"
    When I run pubrules "sync" "--check"
    Then it succeeds
    And it prints "in step with pubrules"

  Scenario: Without a test command, CI runs only the checks, and shellcheck on its shell scripts
    Given a content repository whose ci block is '{}'
    When I run pubrules "sync"
    Then it succeeds
    And "pr.yml" runs the jobs "tested, checks, passed"
    And "main.yml" runs the jobs "tested, checks, passed"
    And in "pr.yml" the "checks" job calls "job-service-checks.yml" with "shellcheck" "**/*.sh"
    And "pr.yml" has no release
    And no workflow pubrules writes scans with SonarQube Cloud

  Scenario: With a test command, CI runs its tooling's checks as the tests job
    Given a content repository whose ci block is '{"with": {"setup": "make venv", "test": "make check", "shellcheck": "bin/*"}}'
    When I run pubrules "sync"
    Then it succeeds
    And "pr.yml" runs the jobs "tested, checks, tests, passed"
    And in "pr.yml" the "tests" job calls "job-service-tests.yml" with "test" "make check"
    And in "pr.yml" the "tests" job calls "job-service-tests.yml" with "setup" "make venv"
    And in "pr.yml" the "checks" job calls "job-service-checks.yml" with "shellcheck" "bin/*"
    And "main.yml" has no release

  Scenario: A content repository has no scan to turn on
    Given a content repository whose ci block is '{"jobs": {"sonar": {"events": ["pr"]}}}'
    When I run pubrules "sync"
    Then it fails
    And stderr mentions "jobs names sonar, which content has not"

  Scenario: Its runners are the ci block's, as for any kind
    Given a content repository whose ci block is '{"runs-on": "my-runners", "with": {"test": "make check"}}'
    When I run pubrules "sync"
    Then it succeeds
    And in "pr.yml" the "tests" job calls "job-service-tests.yml" with "runs-on" "my-runners"
    And in "pr.yml" the "checks" job calls "job-service-checks.yml" with "runs-on" "my-runners"

  Scenario Outline: A content repository's protect block keeps no release tags
    Given a content repository whose protect block is <block>
    When I read its protect entry
    Then its entry has tags <tags> and no release app

    Examples:
      | block                                            | tags  |
      | {"checks": ["ci / passed"]}                      | false |
      | {"checks": ["ci / passed"], "tags": false}       | false |

  Scenario Outline: A content repository's protect block refuses release tags and a release app
    Given a content repository whose protect block is <block>
    When I run pubrules "sync" "--check"
    Then it fails
    And stderr mentions "<message>"

    Examples:
      | block                                               | message                                                    |
      | {"checks": ["ci / passed"], "tags": true}           | "tags" is not for a content repository                     |
      | {"checks": ["ci / passed"], "release_app": 12345}   | "release_app" is not for a content repository              |

  Scenario: Another kind's protect block still defaults to release tags
    Given a service repository whose protect block is {"checks": ["ci / passed"]}
    When I read its protect entry
    Then its entry has tags true and no release app
