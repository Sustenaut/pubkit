Feature: pubkit sync writes a utility's CI from pubkit.json, with no skipped jobs
  A reusable workflow that serves every repository skips the jobs one does
  not use, and GitHub shows each as skipped. With a ci block, pubkit.json
  says which jobs run on which events, and `pubkit sync` writes one
  workflow per event that calls only those, each job its own reusable
  workflow at this pubkit's tag.

  Scenario: A Node package's pull requests and pushes each run only their jobs
    Given a Node repository whose ci block is '{}'
    When I run pubkit "sync"
    Then it succeeds
    And "pr.yml" runs the jobs "test, audit, osv, review, sonar, passed, release"
    And "main.yml" runs the jobs "test, audit, osv, sonar, passed, release"
    And no workflow is written for "nightly"
    And no job in "pr.yml" or "main.yml" has a condition but passed

  Scenario: A Python package runs the functional suite in their place
    Given a Python repository whose ci block is '{"release": false}'
    When I run pubkit "sync"
    Then it succeeds
    And "pr.yml" runs the jobs "functional, audit, osv, review, sonar, passed"

  Scenario: A browser test adds the browser job, and the scan waits for its coverage
    Given a Node repository whose ci block is '{"with": {"browser-test": "npm run test:browser", "coverage-report": "coverage/"}}'
    When I run pubkit "sync"
    Then "pr.yml" runs the jobs "test, browser, audit, osv, review, sonar, passed, release"
    And in "pr.yml" the "sonar" job needs "test, browser"
    And in "pr.yml" the "browser" job is given "browser-test" "npm run test:browser"
    And in "pr.yml" the "test" job is given "coverage-report" "coverage/"

  Scenario: The nightly event writes a scheduled workflow, without the release
    Given a Node repository whose ci block is '{"events": ["pr", "main", "nightly"], "cron": "5 4 * * *"}'
    When I run pubkit "sync"
    Then "nightly.yml" runs the jobs "test, audit, osv, sonar, passed"
    And "nightly.yml" runs on the schedule "5 4 * * *" and by hand

  Scenario: The jobs block narrows a job's events or drops it
    Given a Node repository whose ci block is '{"jobs": {"review": false, "sonar": {"events": ["main"]}}}'
    When I run pubkit "sync"
    Then "pr.yml" runs the jobs "test, audit, osv, passed, release"
    And "main.yml" runs the jobs "test, audit, osv, sonar, passed, release"

  Scenario: The release workflow comes at the pubrel the ci block names
    Given a Node repository whose ci block is '{"release": {"pubrel": "0.5.4", "with": {"semver-checks-version": "0.51.0"}}}'
    When I run pubkit "sync"
    Then "pr.yml" calls pubrel's release workflow at "0.5.4" with "semver-checks-version" "0.51.0"

  Scenario: Every check keeps its name: ci / test, ci / passed, release / release
    Given a Node repository whose ci block is '{}'
    When I run pubkit "sync"
    Then every job in "pr.yml" but release is named "ci", and passed "ci / passed"

  Scenario: A generated workflow that was edited fails the check, and sync restores it
    Given a Node repository whose ci block is '{}'
    When I run pubkit "sync"
    And the file ".github/workflows/pr.yml" reads "name: pr"
    And I run pubkit "sync" "--check"
    Then it fails
    When I run pubkit "sync"
    And I run pubkit "sync" "--check"
    Then it succeeds

  Scenario: A hand-written ci.yml beside a ci block fails the check
    Given a Node repository whose ci block is '{}'
    And the file ".github/workflows/ci.yml" reads "name: ci"
    When I run pubkit "sync"
    And I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "delete .github/workflows/ci.yml"

  Scenario: Without a ci block, the repository's own ci.yml is left alone
    Given an empty repository
    And the file "pubkit.json" reads '{"kind": "utility"}'
    And the file ".github/workflows/ci.yml" reads "name: ci"
    When I run pubkit "sync"
    Then it succeeds
    And no workflow is written for "pr"
    And ".github/workflows/ci.yml" contains "name: ci"

  Scenario Outline: A ci setting pubkit cannot use stops sync with a message
    Given a Node repository whose ci block is '<ci>'
    When I run pubkit "sync"
    Then it fails
    And stderr mentions "<message>"

    Examples:
      | ci                                         | message                                         |
      | {"with": {"tests": "npm test"}}            | with gives tests, which no node job takes       |
      | {"events": ["weekly"]}                     | events must list some of pr, main, nightly      |
      | {"jobs": {"lint": false}}                  | jobs names lint, which node has not             |
      | {"jobs": {"sonar": {"when": "always"}}}    | jobs.sonar must be false or                     |
      | {"language": "go"}                         | language must be one of node, python            |
      | {"matrix": {}}                             | block has no setting matrix                     |
      | {"cron": "daily"}                          | cron must be a schedule of five fields          |
      | {"release": {"pubrel": "latest"}}          | release.pubrel must be a version                |

  Scenario Outline: The job table passes each job exactly the inputs its workflow takes
    When I read the job table for "<language>"
    Then every job's inputs are the ones its workflow declares
    And every job's workflow is pinned and well-formed

    Examples:
      | language |
      | node     |
      | python   |

  Scenario Outline: A job's workflow runs the same steps as the job in the shared workflow
    Then "<job>" runs the steps of the "<name>" job in "<shared>"

    Examples:
      | job                       | name       | shared        |
      | job-node-test.yml         | test       | node-ci.yml   |
      | job-node-browser.yml      | browser    | node-ci.yml   |
      | job-node-audit.yml        | audit      | node-ci.yml   |
      | job-python-functional.yml | functional | python-ci.yml |
      | job-python-browser.yml    | browser    | python-ci.yml |
      | job-python-audit.yml      | audit      | python-ci.yml |
      | job-osv.yml               | osv        | python-ci.yml |
      | job-review.yml            | review     | python-ci.yml |

  Scenario: The nightly moves the pubrel a ci block names, through pubkit.json
    Given a Node repository whose ci block is '{"release": {"pubrel": "0.5.1"}}'
    When I run pubkit "sync"
    And I run pubkit "pins" "--pubrel" "0.5.3" "--pub" "0.2.1" "--no-lock"
    Then it succeeds
    And it prints "pubkit.json: ci.release.pubrel 0.5.1 -> 0.5.3"
    And "pr.yml" calls pubrel's release workflow at "0.5.3" with "pubrel-version" "v0.5.3"
    When I run pubkit "sync" "--check"
    Then it succeeds
