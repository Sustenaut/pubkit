Feature: pubkit sync writes a repository's CI from pubkit.json, with no skipped jobs
  A reusable workflow that serves every repository skips the jobs one does
  not use, and GitHub shows each as skipped. With a ci block, pubkit.json
  says which jobs run on which events, and `pubkit sync` writes one
  workflow per event that calls only those, each job its own reusable
  workflow, pinned to the commit of this pubkit's tag with the version in
  a comment, as SonarQube Cloud's S7637 asks.

  Scenario: A Node package's pull requests and pushes each run only their jobs
    Given a Node repository whose ci block is '{}'
    When I run pubkit "sync"
    Then it succeeds
    And "pr.yml" runs the jobs "test, audit, osv, review, sonar, passed, release"
    And "main.yml" runs the jobs "test, audit, osv, sonar, passed, release"
    And no workflow is written for "nightly"
    And no job in "pr.yml" or "main.yml" has a condition but passed

  Scenario Outline: The token reaches pubkit's jobs by name, which works across owners
    Given a <language> repository whose ci block is '{}'
    When I run pubkit "sync"
    Then it succeeds
    And no written workflow passes "secrets: inherit"
    And every job "<jobs>" in "pr.yml" passes SONAR_TOKEN by name

    Examples:
      | language | jobs               |
      | Node     | sonar              |
      | Python   | sonar              |
      | Rust     | functional, sonar  |

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

  Scenario Outline: The scan reads the code and the tests from where the repository keeps them
    Given a <language> repository whose ci block is '{}'
    And the repository has the directories "<directories>"
    When I run pubkit "sync"
    Then it succeeds
    And in "pr.yml" the "sonar" job is given "layout" "<layout>"

    Examples:
      | language | directories             | layout                                                                  |
      | Python   | src, tests              | -Dsonar.sources=src -Dsonar.tests=tests                                 |
      | Python   | tests                   | -Dsonar.sources=. -Dsonar.tests=tests -Dsonar.exclusions=tests/**       |
      | Rust     | src, tests, fuzz        | -Dsonar.sources=src -Dsonar.tests=tests,fuzz                            |
      | Rust     | crates, tests, benches  | -Dsonar.sources=src,crates -Dsonar.tests=tests,benches                  |

  Scenario Outline: Without tests to name, or for Node, the scan keeps SonarQube Cloud's defaults
    Given a <language> repository whose ci block is '{}'
    And the repository has the directories "<directories>"
    When I run pubkit "sync"
    Then it succeeds
    And in "pr.yml" the "sonar" job is given no "layout"

    Examples:
      | language | directories |
      | Python   | src         |
      | Node     | src, tests  |

  Scenario: A repository's own sonar-args still go to the scan, after the layout
    Given a Python repository whose ci block is '{"with": {"sonar-args": "-Dsonar.tests=test"}}'
    And the repository has the directories "src, tests"
    When I run pubkit "sync"
    Then it succeeds
    And in "pr.yml" the "sonar" job is given "sonar-args" "-Dsonar.tests=test"
    And in "pr.yml" the "sonar" job is given "layout" "-Dsonar.sources=src -Dsonar.tests=tests"

  Scenario: The nightly event writes a scheduled workflow, without the release
    Given a Node repository whose ci block is '{"events": ["pr", "main", "nightly"], "cron": "5 4 * * *"}'
    When I run pubkit "sync"
    Then "nightly.yml" runs the jobs "test, audit, osv, sonar, passed"
    And "nightly.yml" runs on the schedule "5 4 * * *" and by hand

  Scenario: A Rust crate runs the jobs the standard asks for, with its targets
    Given a Rust repository whose ci block is '{}'
    When I run pubkit "sync"
    Then it succeeds
    And "pr.yml" runs the jobs "gates, test, functional, msrv, fuzz, cross, audit, osv, review, sonar, passed, release"
    And "main.yml" runs the jobs "gates, test, functional, msrv, fuzz, cross, audit, osv, sonar, passed, release"
    And "nightly.yml" runs the jobs "gates, test, functional, msrv, fuzz, cross, audit, osv, toolchains, sonar, passed"
    And no job in "pr.yml" or "main.yml" has a condition but passed
    And in "pr.yml" the "gates" job is given "required" "fmt-check lint doc doc-coverage-check publish-check"
    And in "pr.yml" the "audit" job is given "required" "deny vet"
    And in "pr.yml" the "sonar" job needs "functional"

  Scenario: A Rust crate's release checks semver, at the pubrel the ci block names
    Given a Rust repository whose ci block is '{"release": {"pubrel": "0.5.4"}}'
    When I run pubkit "sync"
    Then "pr.yml" calls pubrel's release workflow at "0.5.4" with "semver-checks-version" "0.51.0"

  Scenario: An exception from the standard drops the job it would have asked for
    Given a Rust repository whose ci block is '{}'
    And pubkit.json excepts "RS-VER-02"
    When I run pubkit "sync"
    Then "pr.yml" runs the jobs "gates, test, functional, fuzz, cross, audit, osv, review, sonar, passed, release"

  Scenario: No cross-targets drops the cross job
    Given a Rust repository whose ci block is '{"with": {"cross-targets": "[]"}}'
    When I run pubkit "sync"
    Then "pr.yml" runs the jobs "gates, test, functional, msrv, fuzz, audit, osv, review, sonar, passed, release"

  Scenario: pubrel's own CI calls its release workflow from the checkout
    Given a Rust repository whose ci block is '{"release": {"uses": "local"}}'
    When I run pubkit "sync"
    Then it succeeds
    And "pr.yml" calls the repository's own release workflow
    And "main.yml" calls the repository's own release workflow

  Scenario: A service runs its checks, and Terraform and tests when it names them
    Given a service repository whose ci block is '{"with": {"terraform-dir": ".", "test": "make test"}}'
    When I run pubkit "sync"
    Then it succeeds
    And "pr.yml" runs the jobs "checks, terraform, tests, passed"
    And "pr.yml" has no release
    And in "pr.yml" the "terraform" job is given "terraform-dir" "."

  Scenario: A service without Terraform or tests runs only its checks
    Given a service repository whose ci block is '{}'
    When I run pubkit "sync"
    Then "main.yml" runs the jobs "checks, passed"

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
      | {"release": {"uses": "remote"}}            | release.uses can only be "local"                |
      | {"release": {"uses": "local", "pubrel": "0.5.4"}} | and then names no pubrel                 |

  Scenario Outline: A setting another kind's CI cannot use stops sync with a message
    Given a <kind> repository whose ci block is '<ci>'
    When I run pubkit "sync"
    Then it fails
    And stderr mentions "<message>"

    Examples:
      | kind    | ci                          | message                                   |
      | service | {"release": true}           | release is not for a service              |
      | service | {"with": {"browser-test": "x"}} | with gives browser-test, which no service job takes |
      | Rust    | {"language": "node"}        | language is for a utility                 |

  Scenario Outline: The job table passes each job exactly the inputs its workflow takes
    When I read the job table for "<language>"
    Then every job's inputs are the ones its workflow declares
    And every job's workflow is pinned and well-formed

    Examples:
      | language |
      | node     |
      | python   |
      | rust     |
      | service  |

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
      | job-rust-test.yml         | test       | rust-ci.yml   |
      | job-rust-msrv.yml         | msrv       | rust-ci.yml   |
      | job-rust-toolchains.yml   | toolchains | rust-ci.yml   |
      | job-service-checks.yml    | checks     | service-ci.yml |
      | job-service-terraform.yml | terraform  | service-ci.yml |
      | job-service-tests.yml     | tests      | service-ci.yml |

  Scenario: A released pubkit pins every job to its tag's commit, with the version in a comment
    Given a Node repository whose ci block is '{}'
    And pubkit is a release whose tag names "0f3574a72707a46bae149d3ed7a4644b4837795a"
    When I run pubkit "sync"
    Then it succeeds
    And ".github/workflows/pr.yml" pins pubkit's workflows to "0f3574a72707a46bae149d3ed7a4644b4837795a"
    And ".github/workflows/main.yml" pins pubkit's workflows to "0f3574a72707a46bae149d3ed7a4644b4837795a"
    And "pr.yml" pins pubrel's release workflow to the commit pubkit records
    When I run pubkit "sync" "--check"
    Then it succeeds

  Scenario: A source tree, with no release commit, pins pubkit's jobs by tag
    Given a Node repository whose ci block is '{}'
    When I run pubkit "sync"
    Then it succeeds
    And ".github/workflows/pr.yml" contains "job-osv.yml@v"

  Scenario: pubrel's release workflow is pinned to the commit its tag names, and checked offline
    Given a Node repository whose ci block is '{"release": {"pubrel": "0.5.4"}}'
    And pubrel's repository tags "v0.5.4"
    When I run pubkit "sync"
    Then it succeeds
    And ".github/workflows/pr.yml" pins pubrel's release workflow to the commit of "v0.5.4"
    And ".github/workflows/main.yml" pins pubrel's release workflow to the commit of "v0.5.4"
    When pubrel's repository cannot be reached
    And I run pubkit "sync" "--check"
    Then it succeeds

  Scenario: Without pubrel's tags, its release workflow is pinned by tag, with a warning
    Given a Node repository whose ci block is '{"release": {"pubrel": "0.5.4"}}'
    When I run pubkit "sync"
    Then it succeeds
    And stderr mentions "could not read the commit of v0.5.4"
    And ".github/workflows/pr.yml" contains "release.yml@v0.5.4"

  Scenario Outline: Every reader takes a pin by commit, and the older pin by tag, under any owner
    When every pin reader reads "<line>"
    Then <readers> read owner "<owner>", version "<version>" and commit "<commit>"

    Examples:
      | line                                                                                                        | readers                       | owner     | version | commit                                   |
      | uses: schryer/pubkit/.github/workflows/rust-ci.yml@0f3574a72707a46bae149d3ed7a4644b4837795a # v0.15.2       | conform, bump, nightly, board | schryer   | 0.15.2  | 0f3574a72707a46bae149d3ed7a4644b4837795a |
      | uses: schryer/pubkit/.github/workflows/rust-ci.yml@v0.15.1                                                  | conform, bump, nightly, board | schryer   | 0.15.1  | none                                     |
      | uses: schryer/pubkit/.github/workflows/job-rust-test.yml@0f3574a72707a46bae149d3ed7a4644b4837795a # v0.15.2 | conform, board                | schryer   | 0.15.2  | 0f3574a72707a46bae149d3ed7a4644b4837795a |
      | uses: schryer/pubrel/.github/workflows/release.yml@1a49eb98c9647c1b60073d55b9ca0daf3eeee5c0 # v0.5.4        | release                       | schryer   | 0.5.4   | 1a49eb98c9647c1b60073d55b9ca0daf3eeee5c0 |
      | uses: schryer/pubrel/.github/workflows/release.yml@v0.5.4                                                   | release                       | schryer   | 0.5.4   | none                                     |
      | uses: sustenaut/pubkit/.github/workflows/rust-ci.yml@0f3574a72707a46bae149d3ed7a4644b4837795a # v0.16.0     | conform, bump, nightly, board | sustenaut | 0.16.0  | 0f3574a72707a46bae149d3ed7a4644b4837795a |
      | uses: Sustenaut/pubkit/.github/workflows/job-rust-test.yml@v0.16.0                                          | conform, board                | Sustenaut | 0.16.0  | none                                     |
      | uses: sustenaut/pubrel/.github/workflows/release.yml@1a49eb98c9647c1b60073d55b9ca0daf3eeee5c0 # v0.5.5      | release                       | sustenaut | 0.5.5   | 1a49eb98c9647c1b60073d55b9ca0daf3eeee5c0 |

  Scenario: workflows_owner moves every pubkit and pubrel workflow to another owner
    Given a Node repository whose ci block is '{}'
    And pubkit is a release whose tag names "0f3574a72707a46bae149d3ed7a4644b4837795a"
    When I run pubkit "sync"
    And pubkit.json names the workflows owner "sustenaut"
    And I run pubkit "sync" "--check"
    Then it fails
    When I run pubkit "sync"
    Then it succeeds
    And every pubkit and pubrel workflow ".github/workflows/pr.yml" calls is "sustenaut"'s
    And every pubkit and pubrel workflow ".github/workflows/main.yml" calls is "sustenaut"'s
    And ".github/workflows/pr.yml" contains "sustenaut/pubkit/.github/workflows/job-osv.yml@0f3574a72707a46bae149d3ed7a4644b4837795a # v"
    When I run pubkit "sync" "--check"
    Then it succeeds

  Scenario: Without workflows_owner, pubkit and pubrel are Sustenaut's, where they moved
    Given a Node repository whose ci block is '{}'
    When I run pubkit "sync"
    Then it succeeds
    And every pubkit and pubrel workflow ".github/workflows/pr.yml" calls is "Sustenaut"'s

  Scenario: sync moves workflows written for the old owner to Sustenaut's
    Given a Node repository whose ci block is '{}'
    And pubkit.json names the workflows owner "schryer"
    When I run pubkit "sync"
    Then every pubkit and pubrel workflow ".github/workflows/pr.yml" calls is "schryer"'s
    When pubkit.json names the workflows owner "Sustenaut"
    And I run pubkit "sync" "--check"
    Then it fails
    When I run pubkit "sync"
    Then every pubkit and pubrel workflow ".github/workflows/pr.yml" calls is "Sustenaut"'s

  Scenario Outline: A workflows_owner that is not a GitHub owner stops sync
    Given a Node repository whose ci block is '{}'
    And pubkit.json names the workflows owner "<owner>"
    When I run pubkit "sync"
    Then it fails
    And stderr mentions "workflows_owner must be a GitHub owner"

    Examples:
      | owner         |
      | sustenaut/x   |
      | -sustenaut    |
      | ["sustenaut"] |

  Scenario Outline: sync --check fails on a pubkit action pinned to another pubkit or commit
    Given a Node repository whose ci block is '{}'
    And pubkit is a release whose tag names "0f3574a72707a46bae149d3ed7a4644b4837795a"
    And the workflow "deploy.yml" uses "<owner>"'s pubkit action "deploy-static" at "<pin>"
    When I run pubkit "sync"
    Then it succeeds
    When I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "<message>"

    Examples:
      | owner     | pin            | message                                           |
      | Sustenaut | another pubkit | deploy-static is at pubkit 0.0.1                  |
      | schryer   | another pubkit | deploy-static is at pubkit 0.0.1                  |
      | Sustenaut | another commit | deploy-static@1a49eb98c9647c1b60073d55b9ca0daf3eeee5c0 is not the commit |

  Scenario: A stale deploy-static pin fails sync --check until it is moved, then passes
    Given a Node repository whose ci block is '{}'
    And pubkit is a release whose tag names "0f3574a72707a46bae149d3ed7a4644b4837795a"
    And the workflow "deploy.yml" uses "schryer"'s pubkit action "deploy-static" at "another pubkit"
    When I run pubkit "sync"
    Then it succeeds
    When I run pubkit "sync" "--check"
    Then it fails
    When I run pubkit "pins" "--pubrel" "0.5.3" "--pub" "0.2.1" "--no-lock"
    And I run pubkit "sync" "--check"
    Then it succeeds
    And ".github/workflows/deploy.yml" uses "Sustenaut"'s pubkit action "deploy-static" at "this pubkit's commit"

  Scenario Outline: sync --check takes this pubkit's pin of an action, and reports one with no version
    Given a Node repository whose ci block is '{}'
    And pubkit is a release whose tag names "0f3574a72707a46bae149d3ed7a4644b4837795a"
    And the workflow "deploy.yml" uses "Sustenaut"'s pubkit action "deploy-static" at "<pin>"
    When I run pubkit "sync"
    Then it succeeds
    When I run pubkit "sync" "--check"
    Then it succeeds
    And stderr <reports>

    Examples:
      | pin                  | reports                                                    |
      | this pubkit's commit | does not mention "deploy-static"                           |
      | this pubkit's tag    | does not mention "deploy-static"                           |
      | main                 | mentions "Sustenaut/pubkit/actions/deploy-static@main names no pubkit version" |

  Scenario: A repository that deploys with deploy-static has its site checked on pull requests
    Given a Node repository whose ci block is '{}'
    And the workflow "deploy.yml" deploys "public" with deploy-static
    When I run pubkit "sync"
    Then it succeeds
    And "pr.yml" checks the site in "public" after running "nothing", deploying nothing
    And "main.yml" has no site job

  Scenario Outline: The site block names the pull request's build, the directory and the address
    Given a Node repository whose ci block is '{}'
    And the workflow "deploy.yml" deploys "public" with deploy-static
    And pubkit.json's site block is '<site>'
    When I run pubkit "sync"
    Then it succeeds
    And "pr.yml" checks the site in "<folder>" after running "<build>", deploying nothing

    Examples:
      | site                                                                   | folder | build       |
      | {"pr_build": "make sample", "build": "make site", "check_dir": "site"} | site   | make sample |
      | {"build": "make site"}                                                 | public | make site   |
      | {"check_dir": "site"}                                                  | site   | nothing     |

  Scenario: A site block alone, without a deploy workflow, has the site checked too
    Given a Node repository whose ci block is '{}'
    And pubkit.json's site block is '{"check_dir": "public", "url": "https://example.eu/"}'
    When I run pubkit "sync"
    Then it succeeds
    And "pr.yml" checks the site in "public" after running "nothing", deploying nothing
    And in "pr.yml" the site check is given "site-url" "https://example.eu/"

  Scenario: A repository that deploys no site has no site job
    Given a Node repository whose ci block is '{}'
    When I run pubkit "sync"
    Then it succeeds
    And "pr.yml" has no site job

  Scenario Outline: A site setting pubkit cannot use stops sync with a message
    Given a Node repository whose ci block is '{}'
    And pubkit.json's site block is '<site>'
    When I run pubkit "sync"
    Then it fails
    And stderr mentions "<message>"

    Examples:
      | site                                       | message                                         |
      | {"check_dir": "public", "deploy": "x"}     | site block has no setting deploy                |
      | {"check_dir": 3}                           | check_dir must be one line of text              |
      | {"check_dir": "../out"}                    | check_dir must be a directory inside            |
      | {"check_dir": "public", "url": "http://x"} | url must be the site's https:// address         |
      | {"pr_build": "make sample"}                | site block needs check_dir                      |
      | ["public"]                                 | site block must be an object                    |

  Scenario: The nightly moves the pubrel a ci block names, through pubkit.json
    Given a Node repository whose ci block is '{"release": {"pubrel": "0.5.1"}}'
    When I run pubkit "sync"
    And I run pubkit "pins" "--pubrel" "0.5.3" "--pub" "0.2.1" "--no-lock"
    Then it succeeds
    And it prints "pubkit.json: ci.release.pubrel 0.5.1 -> 0.5.3"
    And "pr.yml" calls pubrel's release workflow at "0.5.3" with "pubrel-version" "v0.5.3"
    When I run pubkit "sync" "--check"
    Then it succeeds
