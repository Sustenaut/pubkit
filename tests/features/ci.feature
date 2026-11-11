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
    And "pr.yml" runs the jobs "tested, test, audit, osv, review, sonar, passed, release"
    And "main.yml" runs the jobs "tested, test, audit, osv, sonar, passed, release"
    And no workflow is written for "nightly"
    And no workflow is written for "queue"
    And in "pr.yml" every job but tested, the scan, passed and release runs only when it did not

  Scenario Outline: Main skips the tests the merge queue already passed on its commit
    Given a <language> repository whose ci block is '{}'
    When I run pubkit "sync"
    Then "main.yml" first asks whether the merge queue passed its commit in "<queued>"
    And in "main.yml" every job but tested, the scan, passed and release runs only when it did not
    And in "main.yml" the scan runs either way, reading the coverage of the queue's run when it skipped the tests
    And in "main.yml" passed takes a job skipped for the queue's run as passed
    And in "main.yml" the release runs whenever passed succeeded

    Examples:
      | language | queued    |
      | Node     | pr.yml    |
      | Python   | pr.yml    |
      | Rust     | queue.yml |
      | service  | pr.yml    |

  Scenario Outline: The merge queue skips the tests the pull request already passed on the same tree
    Given a <language> repository whose ci block is '{}'
    When I run pubkit "sync"
    Then "pr.yml" first asks whether the merge queue passed its commit in "pr.yml"
    And in "pr.yml" every job but tested, the scan, passed and release runs only when it did not
    And in "pr.yml" the scan runs only when it did not, as main's push scans the tree
    And in "pr.yml" passed takes a job skipped for the queue's run as passed
    And in "pr.yml" the release runs whenever passed succeeded

    Examples:
      | language |
      | Node     |
      | Python   |
      | service  |

  Scenario Outline: job-tested finds the run that tested this commit's tree
    Given GitHub holds <runs>
    When job-tested runs on a <event> of commit "<commit>"
    Then it says tested "<tested>", run "<run>"

    Examples:
      | runs                                                                         | event        | commit | tested | run |
      | a pull request run 11 of commit "pr" with tree "T"                           | pull_request | pr     | false  |     |
      | a pull request run 11 of commit "pr" with tree "T"                           | merge_group  | queue  | true   | 11  |
      | a pull request run 11 of commit "pr" with tree "U"                           | merge_group  | queue  | false  |     |
      | a pull request run 11 of commit "pr" with tree "T", its artifacts expired    | merge_group  | queue  | false  |     |
      | a pull request run 11 of commit "pr" with tree "T", and a queue run 12 of commit "queue" that took its tests from run 11 | push | queue | true | 11 |
      | a queue run 12 of commit "queue" with tree "T" that ran its tests            | push         | queue  | true   | 12  |

  Scenario: Main runs a job the merge queue does not, whatever the queue passed
    Given a Node repository whose ci block is '{"jobs": {"osv": {"events": ["main"]}}}'
    When I run pubkit "sync"
    Then "pr.yml" runs the jobs "tested, test, audit, review, sonar, passed, release"
    And in "main.yml" the "osv" job runs whatever the queue passed

  Scenario: Without a pull request workflow there is no queue run, and main tests everything
    Given a Node repository whose ci block is '{"events": ["main"]}'
    When I run pubkit "sync"
    Then "main.yml" runs the jobs "test, audit, osv, sonar, passed, release"
    And no job in "main.yml" has a condition but passed

  Scenario: The nightly tests everything, whatever the queue passed
    Given a Node repository whose ci block is '{"events": ["pr", "main", "nightly"]}'
    When I run pubkit "sync"
    Then no job in "nightly.yml" has a condition but passed

  Scenario Outline: The merge queue's runs report the checks a pull request's do
    Given a <language> repository whose ci block is '{}'
    When I run pubkit "sync"
    Then "pr.yml" runs on pull requests and in the merge queue
    And "main.yml" runs on pushes to main alone

    Examples:
      | language |
      | Node     |
      | Python   |
      | service  |

  Scenario: A Rust crate's pull requests skip the heavy jobs, which the merge queue runs before anything merges
    Given a Rust repository whose ci block is '{}'
    When I run pubkit "sync"
    Then "pr.yml" runs on pull requests alone
    And "queue.yml" runs in the merge queue alone
    And "pr.yml" runs the jobs "gates, test, functional, audit, osv, review, sonar, passed, release"
    And "queue.yml" runs the jobs "gates, test, functional, msrv, fuzz, cross, audit, osv, sonar, passed, release"
    And no job in "queue.yml" has a condition but passed

  Scenario: Without the queue event, a Rust crate's pull requests run the heavy jobs, in the queue too
    Given a Rust repository whose ci block is '{"events": ["pr", "main", "nightly"]}'
    When I run pubkit "sync"
    Then "pr.yml" runs on pull requests and in the merge queue
    And "pr.yml" runs the jobs "tested, gates, test, functional, msrv, fuzz, cross, audit, osv, review, sonar, passed, release"
    And no workflow is written for "queue"
    And "main.yml" first asks whether the merge queue passed its commit in "pr.yml"

  Scenario: The merge queue checks the site a pull request checks
    Given a Rust repository whose ci block is '{}'
    And the workflow "deploy.yml" deploys "public" with deploy-static
    When I run pubkit "sync"
    Then it succeeds
    And "pr.yml" checks the site in "public" after running "nothing", deploying nothing
    And "queue.yml" checks the site in "public" after running "nothing", deploying nothing
    And "main.yml" has no site job

  Scenario: pubkit's own CI runs in the merge queue, and builds the wheel from a push to main alone
    When I read pubkit's own ci.yml
    Then it runs on pull requests, in the merge queue and on pushes to main
    And its wheel job runs only on a push to main
    And in the merge queue and on a push to main it skips the tests an earlier run passed on the same tree

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
    And "pr.yml" runs the jobs "tested, functional, audit, osv, review, sonar, passed"

  Scenario: A browser test adds the browser job, and the scan waits for its coverage
    Given a Node repository whose ci block is '{"with": {"browser-test": "npm run test:browser", "coverage-report": "coverage/"}}'
    When I run pubkit "sync"
    Then "pr.yml" runs the jobs "tested, test, browser, audit, osv, review, sonar, passed, release"
    And in "pr.yml" the "sonar" job needs "tested, test, browser"
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
    And "pr.yml" runs the jobs "gates, test, functional, audit, osv, review, sonar, passed, release"
    And "queue.yml" runs the jobs "gates, test, functional, msrv, fuzz, cross, audit, osv, sonar, passed, release"
    And "main.yml" runs the jobs "tested, gates, test, functional, msrv, fuzz, cross, audit, osv, sonar, passed, release"
    And "nightly.yml" runs the jobs "gates, test, functional, msrv, fuzz, cross, audit, osv, toolchains, sonar, passed"
    And no job in "pr.yml" has a condition but passed
    And in "main.yml" every job but tested, the scan, passed and release runs only when it did not
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
    Then "queue.yml" runs the jobs "gates, test, functional, fuzz, cross, audit, osv, sonar, passed, release"

  Scenario: No cross-targets drops the cross job
    Given a Rust repository whose ci block is '{"with": {"cross-targets": "[]"}}'
    When I run pubkit "sync"
    Then "queue.yml" runs the jobs "gates, test, functional, msrv, fuzz, audit, osv, sonar, passed, release"

  Scenario: pubrel's own CI calls its release workflow from the checkout
    Given a Rust repository whose ci block is '{"release": {"uses": "local"}}'
    When I run pubkit "sync"
    Then it succeeds
    And "pr.yml" calls the repository's own release workflow
    And "main.yml" calls the repository's own release workflow

  Scenario: A service runs its checks and its scan, and Terraform and tests when it names them
    Given a service repository whose ci block is '{"with": {"terraform-dir": ".", "test": "make test"}}'
    When I run pubkit "sync"
    Then it succeeds
    And "pr.yml" runs the jobs "tested, checks, terraform, tests, sonar, passed"
    And "pr.yml" has no release
    And in "pr.yml" the "terraform" job is given "terraform-dir" "."

  Scenario: A service without Terraform or tests runs only its checks and its scan
    Given a service repository whose ci block is '{}'
    When I run pubkit "sync"
    Then "main.yml" runs the jobs "tested, checks, sonar, passed"

  Scenario: A private service drops the scan
    Given a service repository whose ci block is '{"jobs": {"sonar": false}}'
    When I run pubkit "sync"
    Then it succeeds
    And "pr.yml" runs the jobs "tested, checks, passed"

  Scenario: The jobs block narrows a job's events or drops it
    Given a Node repository whose ci block is '{"jobs": {"review": false, "sonar": {"events": ["main"]}}}'
    When I run pubkit "sync"
    Then "pr.yml" runs the jobs "tested, test, audit, osv, passed, release"
    And "main.yml" runs the jobs "tested, test, audit, osv, sonar, passed, release"

  Scenario Outline: The release workflow gets the release app's secrets from pubrel 0.6.1
    Given a Node repository whose ci block is '{"release": {"pubrel": "<pubrel>"}}'
    When I run pubkit "sync"
    Then in "pr.yml" and "main.yml" the release is <given> the release app's secrets

    Examples:
      | pubrel | given     |
      | 0.6.0  | not given |
      | 0.6.1  | given     |
      | 0.7.0  | given     |
      | 1.0.0  | given     |

  Scenario: The repository's own release workflow gets the release app's secrets
    Given a Rust repository whose ci block is '{"release": {"uses": "local"}}'
    When I run pubkit "sync"
    Then in "pr.yml" and "main.yml" the release is given the release app's secrets

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

  Scenario: The tools a Rust repository lists reach its functional job
    Given a Rust repository whose ci block is '{"tools": [{"crate": "some-cli", "git": "https://github.com/example-org/some-cli", "tag": "v1.2.3"}]}'
    When I run pubkit "sync"
    Then it succeeds
    And "pr.yml"'s job "functional" is given the tools "[{"crate": "some-cli", "git": "https://github.com/example-org/some-cli", "tag": "v1.2.3"}]"
    And "pr.yml"'s job "gates" is given no tools

  Scenario: Without tools the functional job is given none
    Given a Rust repository whose ci block is '{}'
    When I run pubkit "sync"
    Then "pr.yml"'s job "functional" is given no tools

  Scenario Outline: Each workflow that runs the functional suite installs every tool, and fails when one fails
    When the "<workflow>" step that installs tools runs on two tools
    Then it installed both with cargo at their tags, into the tools directory
    When the "<workflow>" step that installs tools runs with cargo failing
    Then the step fails

    Examples:
      | workflow                |
      | job-rust-functional.yml |
      | rust-ci.yml             |


  Scenario Outline: A setup that fails is tried again, three times at most
    When the "<workflow>" setup step runs a setup that fails the first time
    Then the setup ran 2 times, and the step passed
    When the "<workflow>" setup step runs a setup that always fails
    Then the setup ran 3 times, and the step failed

    Examples:
      | workflow                  |
      | python-ci.yml             |
      | job-python-functional.yml |
      | job-python-browser.yml    |
      | job-service-tests.yml     |
      | service-ci.yml            |
      | node-ci.yml               |
      | job-node-test.yml         |
      | job-node-browser.yml      |

  Scenario: Every hash-locked install and every pip-audit in the reusable workflows is tried three times, the last pip-audit from the wheel mirror
    Then every step that installs tests/requirements.lock does it through tests/install-lock
    And every pip-audit is tried three times, each try after a failed one

  Scenario: tests/install-lock tries the lock three times, then once more with our release wheels from the mirror
    Given a lock that pins "pubkit" by a GitHub release URL and "pytest" from PyPI
    And pubkit.json's wheel mirror is "https://storage.example/wheels"
    When tests/install-lock runs with a pip that fails every install of a GitHub release URL
    Then pip was run 4 times, and the install passed
    And the last lock pinned "pubkit" at the mirror's address for its sha256, with its hash
    And the last lock left "pytest" as it was

  Scenario: With a PyPI mirror, tests/install-lock's last try takes the other packages from its simple index
    Given a lock that pins "pubkit" by a GitHub release URL and "pytest" from PyPI
    And pubkit.json's wheel mirror is "https://storage.example/wheels"
    And pubkit.json's PyPI mirror is "https://storage.example/pypi"
    When tests/install-lock runs with a pip that fails every install without an index of its own
    Then pip was run 4 times, and the install passed
    And the last install took its packages from "https://storage.example/pypi/simple/"
    And the last lock pinned "pubkit" at the mirror's address for its sha256, with its hash

  Scenario: Without a wheel mirror, tests/install-lock stops after three tries
    Given a lock that pins "pubkit" by a GitHub release URL and "pytest" from PyPI
    When tests/install-lock runs with a pip that fails every install of a GitHub release URL
    Then pip was run 3 times, and the install failed

  Scenario Outline: A wheel mirror that is not one https base stops sync with a message
    Given pubkit.json's mirror setting '<mirror>'
    Then sync stops, saying what a mirror setting is

    Examples:
      | mirror                                          |
      | {"wheels": "http://storage.example/wheels"}    |
      | {"wheels": "https://storage.example/wheels/"}  |
      | {"wheels": 3}                                   |
      | {"npm": "https://storage.example/npm"}         |
      | {"pypi": "https://storage.example/pypi/"}      |
      | {"terraform": "https://storage.example/tf/"}   |
      | "https://storage.example/wheels"                |

  Scenario: The launcher takes pubkit's own wheel from the mirror when GitHub fails, checked against the lock's sha256
    Given pubkit.json's wheel mirror is "https://storage.example/wheels"
    And a lock that pins this pubkit's wheel
    When the launcher fetches pubkit with GitHub failing and the mirror giving "the wheel"
    Then uvx is given the cached wheel
    When the launcher fetches pubkit with GitHub failing and the mirror giving "another file"
    Then the launcher stops with "is not the wheel tests/requirements.lock pins"

  Scenario: Without a wheel mirror, the launcher is as it was
    Then the launcher without a mirror runs uvx on pubkit's GitHub release URL, with no fetch
  Scenario Outline: A Terraform init that fails is tried three times, then once from pubkit.json's provider mirror
    When the "<workflow>" init step runs with a terraform that fails unless given a provider mirror, and the mirror "https://storage.example/terraform"
    Then terraform init ran 4 times, and the step passed
    And the last init's CLI configuration installs every provider from "https://storage.example/terraform/"
    When the "<workflow>" init step runs with a terraform that fails unless given a provider mirror, and no mirror
    Then terraform init ran 3 times, and the step failed

    Examples:
      | workflow                  |
      | job-service-terraform.yml |
      | service-ci.yml            |

  Scenario: pubkit's workflows use pubkit's actions at one released commit, never by a local path
    A reusable workflow runs in the caller's repository, where ./actions/ is
    the caller's own, so pubkit's workflows name pubkit's actions as any
    other repository does: by a release's commit, no later than this pubkit.
    Then every use of pubkit's actions in pubkit's workflows pins one commit, at a release no later than this pubkit
    And no reusable workflow of pubkit's uses an action by a local path

  Scenario: Without a list, the review leaves pubkit's and pubrel's workflows and actions out of the license check alone
    Then job-review.yml's license exceptions name every reusable workflow and action pubkit has, and pubrel's release workflow
    And job-review.yml sets no other input, so advisories apply to everything

  Scenario: sync lists the owner's workflows and actions the repository calls, for the review's license check
    Given a Node repository whose ci block is '{}'
    And a deploy workflow that calls integrations' scan, pubkit's cache action, actions/checkout and a local action
    When I run pubkit "sync"
    Then ".github/dependency-review-licenses.txt" lists "pkg:githubactions/Sustenaut/integrations/.github/workflows/scan.yml"
    And ".github/dependency-review-licenses.txt" lists "pkg:githubactions/Sustenaut/pubkit/actions/cache"
    And ".github/dependency-review-licenses.txt" lists "pkg:githubactions/Sustenaut/pubkit/.github/workflows/job-review.yml"
    And ".github/dependency-review-licenses.txt" names neither actions/checkout nor the local action
    When I run pubkit "sync" "--check"
    Then the exit code is 0

  Scenario: Without the review job there is no list
    Given a Node repository whose ci block is '{"jobs": {"review": false}}'
    When I run pubkit "sync"
    Then ".github/dependency-review-licenses.txt" is not there

  Scenario Outline: The review takes only the owner's purls from the list, and pubkit's and pubrel's without one
    When the "<workflow>" review exceptions step runs on a list holding another owner's purl
    Then it passes the owner's purls alone
    When the "<workflow>" review exceptions step runs without a list
    Then it passes pubkit's and pubrel's

    Examples:
      | workflow        |
      | job-review.yml  |
      | python-ci.yml   |
      | node-ci.yml     |
      | rust-ci.yml     |

  Scenario: pins moves a tool to the latest release of its git repository
    Given a Rust repository whose ci block is '{"tools": [{"crate": "some-cli", "git": "https://github.com/example-org/some-cli.git", "tag": "v1.2.3"}]}'
    And the latest release of "example-org/some-cli" is "v1.3.0"
    When I run pubkit "pins" "--no-lock"
    Then it succeeds
    And it prints "pubkit.json: ci.tools some-cli v1.2.3 -> v1.3.0"
    And "pubkit.json" contains "v1.3.0"
    And ".github/workflows/pr.yml" contains "v1.3.0"
    And ".github/workflows/pr.yml" does not contain "v1.2.3"

  Scenario: pins leaves a tool already at its latest release
    Given a Rust repository whose ci block is '{"tools": [{"crate": "some-cli", "git": "https://github.com/example-org/some-cli", "tag": "v1.3.0"}]}'
    And the latest release of "example-org/some-cli" is "v1.3.0"
    When I run pubkit "pins" "--no-lock"
    Then it succeeds
    And "pubkit.json" contains "v1.3.0"
    And "pins" reported no tool moving

  Scenario Outline: pins leaves a tool whose latest release it cannot trust, and warns
    Given a Rust repository whose ci block is '{"tools": [{"crate": "some-cli", "git": "<git>", "tag": "v1.2.3"}]}'
    And the latest release of "example-org/some-cli" is "<latest>"
    When I run pubkit "pins" "--no-lock"
    Then it succeeds
    And stderr mentions "so some-cli stays at v1.2.3"
    And "pubkit.json" contains "v1.2.3"

    Examples:
      | git                                         | latest      |
      | https://github.com/example-org/missing      | v1.3.0      |
      | https://github.com/example-org/some-cli     | v1; rm -rf  |
      | https://git.example.eu/example-org/some-cli | v1.3.0      |

  Scenario Outline: A tool pubkit cannot install stops sync with a message
    Given a Rust repository whose ci block is '<ci>'
    When I run pubkit "sync"
    Then it fails
    And stderr mentions "<message>"

    Examples:
      | ci | message |
      | {"tools": "publet-cli"} | tools must be a list |
      | {"tools": ["publet-cli"]} | tools must list objects |
      | {"tools": [{"crate": "a", "git": "https://example.eu/a", "tag": "v1", "rev": "b"}]} | tools has no setting rev |
      | {"tools": [{"git": "https://example.eu/a", "tag": "v1"}]} | tools: crate must be a crate's name |
      | {"tools": [{"crate": "a b", "git": "https://example.eu/a", "tag": "v1"}]} | tools: crate must be a crate's name |
      | {"tools": [{"crate": "a", "tag": "v1"}]} | tools: git must be the git repository's https:// address |
      | {"tools": [{"crate": "a", "git": "http://example.eu/a", "tag": "v1"}]} | tools: git must be the git repository's https:// address |
      | {"tools": [{"crate": "a", "git": "https://example.eu/a; rm", "tag": "v1"}]} | tools: git must be the git repository's https:// address |
      | {"tools": [{"crate": "a", "git": "https://example.eu/a"}]} | tools: tag must be a tag of that repository |
      | {"tools": [{"crate": "a", "git": "https://example.eu/a", "tag": "-v1"}]} | tools: tag must be a tag of that repository |
      | {"tools": [{"crate": "a", "git": "https://example.eu/a", "tag": "v1"}, {"crate": "a", "git": "https://example.eu/b", "tag": "v1"}]} | tools lists a crate twice |

  Scenario: tools is for a Rust repository
    Given a Node repository whose ci block is '{"tools": [{"crate": "a", "git": "https://example.eu/a", "tag": "v1"}]}'
    When I run pubkit "sync"
    Then it fails
    And stderr mentions "tools is for a Rust repository's functional job"

  Scenario Outline: A ci setting pubkit cannot use stops sync with a message
    Given a Node repository whose ci block is '<ci>'
    When I run pubkit "sync"
    Then it fails
    And stderr mentions "<message>"

    Examples:
      | ci                                         | message                                         |
      | {"with": {"tests": "npm test"}}            | with gives tests, which no node job takes       |
      | {"events": ["weekly"]}                     | events must list some of pr, queue, main, nightly |
      | {"events": ["queue", "main"]}              | events lists queue without pr                   |
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
      | uses: example-org/pubkit/.github/workflows/rust-ci.yml@0f3574a72707a46bae149d3ed7a4644b4837795a # v0.15.2       | conform, bump, nightly        | example-org   | 0.15.2  | 0f3574a72707a46bae149d3ed7a4644b4837795a |
      | uses: example-org/pubkit/.github/workflows/rust-ci.yml@v0.15.1                                                  | conform, bump, nightly        | example-org   | 0.15.1  | none                                     |
      | uses: example-org/pubkit/.github/workflows/job-rust-test.yml@0f3574a72707a46bae149d3ed7a4644b4837795a # v0.15.2 | conform                       | example-org   | 0.15.2  | 0f3574a72707a46bae149d3ed7a4644b4837795a |
      | uses: example-org/pubrel/.github/workflows/release.yml@1a49eb98c9647c1b60073d55b9ca0daf3eeee5c0 # v0.5.4        | release                       | example-org   | 0.5.4   | 1a49eb98c9647c1b60073d55b9ca0daf3eeee5c0 |
      | uses: example-org/pubrel/.github/workflows/release.yml@v0.5.4                                                   | release                       | example-org   | 0.5.4   | none                                     |
      | uses: sustenaut/pubkit/.github/workflows/rust-ci.yml@0f3574a72707a46bae149d3ed7a4644b4837795a # v0.16.0     | conform, bump, nightly        | sustenaut | 0.16.0  | 0f3574a72707a46bae149d3ed7a4644b4837795a |
      | uses: Sustenaut/pubkit/.github/workflows/job-rust-test.yml@v0.16.0                                          | conform                       | Sustenaut | 0.16.0  | none                                     |
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
    And pubkit.json names the workflows owner "example-org"
    When I run pubkit "sync"
    Then every pubkit and pubrel workflow ".github/workflows/pr.yml" calls is "example-org"'s
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
      | example-org/x  |
      | -example-org   |
      | ["example-org"] |

  Scenario Outline: sync --check fails on a pubkit action pinned to another pubkit or commit
    Given a Node repository whose ci block is '{}'
    And pubkit is a release whose tag names "0f3574a72707a46bae149d3ed7a4644b4837795a"
    And the workflow "deploy.yml" uses "<owner>"'s pubkit action "cache" at "<pin>"
    When I run pubkit "sync"
    Then it succeeds
    When I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "<message>"

    Examples:
      | owner     | pin            | message                                           |
      | Sustenaut | another pubkit | cache@v0.0.1 is not pubkit                        |
      | example-org   | another pubkit | cache@v0.0.1 is not pubkit                        |
      | Sustenaut | another commit | cache@1a49eb98c9647c1b60073d55b9ca0daf3eeee5c0 is not the commit |

  Scenario Outline: sync --check fails on a hand-written call of a pubkit workflow at another pubkit
    Given a service repository whose ci block is '{}'
    And pubkit is a release whose tag names "0f3574a72707a46bae149d3ed7a4644b4837795a"
    When I run pubkit "sync"
    Then it succeeds
    Given the workflow "ci-extra.yml" calls "<owner>"'s pubkit workflow "service-ci.yml" at "<pin>"
    When I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "<message>"

    Examples:
      | owner     | pin            | message                                                 |
      | Sustenaut | another pubkit | ci-extra.yml: service-ci.yml@v0.0.1 is not pubkit       |
      | example-org   | another pubkit | ci-extra.yml: service-ci.yml@v0.0.1 is not pubkit       |
      | Sustenaut | another commit | service-ci.yml@1a49eb98c9647c1b60073d55b9ca0daf3eeee5c0 is not the commit |

  Scenario: A stale hand-written service-ci.yml pin fails sync --check until it is moved, then passes
    Given a service repository whose ci block is '{}'
    And pubkit is a release whose tag names "0f3574a72707a46bae149d3ed7a4644b4837795a"
    And the workflow "ci-extra.yml" calls "Sustenaut"'s pubkit workflow "service-ci.yml" at "another pubkit"
    When I run pubkit "sync"
    And I run pubkit "sync" "--check"
    Then it fails
    When I run pubkit "pins" "--pubrel" "0.5.3" "--no-lock"
    And I run pubkit "sync" "--check"
    Then it succeeds
    And ".github/workflows/ci-extra.yml" calls "Sustenaut"'s pubkit workflow "service-ci.yml" at "this pubkit's commit"

  Scenario: A stale pubkit action pin fails sync --check until it is moved, then passes
    Given a Node repository whose ci block is '{}'
    And pubkit is a release whose tag names "0f3574a72707a46bae149d3ed7a4644b4837795a"
    And the workflow "deploy.yml" uses "example-org"'s pubkit action "cache" at "another pubkit"
    When I run pubkit "sync"
    Then it succeeds
    When I run pubkit "sync" "--check"
    Then it fails
    When I run pubkit "pins" "--pubrel" "0.5.3" "--no-lock"
    And I run pubkit "sync"
    And I run pubkit "sync" "--check"
    Then it succeeds
    And ".github/workflows/deploy.yml" uses "Sustenaut"'s pubkit action "cache" at "this pubkit's commit"

  Scenario Outline: sync --check takes this pubkit's pin of an action, and reports one with no version
    Given a Node repository whose ci block is '{}'
    And pubkit is a release whose tag names "0f3574a72707a46bae149d3ed7a4644b4837795a"
    And the workflow "deploy.yml" uses "Sustenaut"'s pubkit action "cache" at "<pin>"
    When I run pubkit "sync"
    Then it succeeds
    When I run pubkit "sync" "--check"
    Then it succeeds
    And stderr <reports>

    Examples:
      | pin                  | reports                                                    |
      | this pubkit's commit | does not mention "cache@"                                  |
      | this pubkit's tag    | does not mention "cache@"                                  |
      | main                 | mentions "Sustenaut/pubkit/actions/cache@main names no pubkit version" |

  Scenario: sync --check fails on a pubkit action this pubkit does not ship, and pins leaves it where it is
    Given a Node repository whose ci block is '{}'
    And pubkit is a release whose tag names "0f3574a72707a46bae149d3ed7a4644b4837795a"
    And the workflow "deploy.yml" uses "Sustenaut"'s pubkit action "deploy-ssh" at "another pubkit"
    When I run pubkit "sync"
    And I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "deploy.yml: pubkit"
    And stderr mentions "has no actions/deploy-ssh; it moved out of pubkit"
    When I run pubkit "pins" "--pubrel" "0.5.3" "--no-lock"
    Then it prints "deploy.yml: actions/deploy-ssh stays at pubkit 0.0.1"
    And ".github/workflows/deploy.yml" uses "Sustenaut"'s pubkit action "deploy-ssh" at "another pubkit"

  Scenario: sync --check fails on a pubkit Terraform module, which pubkit no longer ships
    Given a Node repository whose ci block is '{}'
    And the file "infra/main.tf" reads "module "dns" { source = "github.com/Sustenaut/pubkit//terraform/hosting?ref=v0.24.4" }"
    When I run pubkit "sync"
    And I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "infra/main.tf: pubkit"
    And stderr mentions "has no terraform/hosting; it moved out of pubkit"

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

  Scenario: A site block gives the site check its address
    Given a Node repository whose ci block is '{}'
    And the workflow "deploy.yml" deploys "public" with deploy-static
    And pubkit.json's site block is '{"check_dir": "public", "url": "https://example.eu/"}'
    When I run pubkit "sync"
    Then it succeeds
    And "pr.yml" checks the site in "public" after running "nothing", deploying nothing
    And in "pr.yml" the site check is given "site-url" "https://example.eu/"

  Scenario: A site block without a deploy workflow stops sync: the check runs the deploy's own deploy-static
    Given a Node repository whose ci block is '{}'
    And pubkit.json's site block is '{"check_dir": "public"}'
    When I run pubkit "sync"
    Then it fails
    And stderr mentions "no workflow of the repository's own uses deploy-static"

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
    And I run pubkit "pins" "--pubrel" "0.5.3" "--no-lock"
    Then it succeeds
    And it prints "pubkit.json: ci.release.pubrel 0.5.1 -> 0.5.3"
    And "pr.yml" calls pubrel's release workflow at "0.5.3" with "pubrel-version" "v0.5.3"
    When I run pubkit "sync" "--check"
    Then it succeeds

  Scenario: sync declares the sonar job's token where the repository lists its integrations
    Given a Node repository whose ci block is '{}'
    And the file "integrations.json" reads "{"uses": [{"plugin": "github.app-token", "paths": ["a.yml"], "mode": "required"}]}"
    When I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "integrations.json doesn't declare what the files pubkit writes use"
    When I run pubkit "sync"
    Then "integrations.json" contains ""plugin": "sonarcloud.scan""
    And "integrations.json" contains "".github/workflows/pr.yml""
    And "integrations.json" contains ""plugin": "github.app-token""
    When I run pubkit "sync" "--check"
    Then it succeeds

  Scenario: A token already declared, by name or pattern, leaves integrations.json alone
    Given a Node repository whose ci block is '{}'
    And the file "integrations.json" reads "{"uses": [{"plugin": "sonarcloud.read", "paths": [".github/workflows/*.yml"], "mode": "required"}]}"
    When I run pubkit "sync"
    Then "integrations.json" does not contain "sonarcloud.scan"

  Scenario: A repository with no integrations.json gets none
    Given a Node repository whose ci block is '{}'
    When I run pubkit "sync"
    Then "integrations.json" is not there

  Scenario: Without the sonar job, nothing is declared
    Given a Node repository whose ci block is '{"jobs": {"sonar": false}}'
    And the file "integrations.json" reads "{"uses": []}"
    When I run pubkit "sync"
    Then "integrations.json" does not contain "sonarcloud"

  # The checks a ruleset requires are only passed if something reports them
  # on the commit it waits on: the merge queue's, or with no queue the pull
  # request's. sync holds pubkit.json's protect.checks to what the
  # repository's workflows report there. (The second sync writes what the
  # first one's tests/ directory changes.)

  Scenario Outline: sync --check takes the checks the generated CI reports in the merge queue
    Given a <language> repository whose ci block is '<ci>'
    And pubkit.json's protect block is {"checks": [<checks>]}
    When I run pubkit "sync"
    And I run pubkit "sync"
    And I run pubkit "sync" "--check"
    Then it prints "in step with pubkit"
    And stderr does not mention "protect.checks"

    Examples:
      | language | ci              | checks                             |
      | Python   | {"release": {}} | "ci / passed", "release / release" |
      | Node     | {}              | "ci / passed"                      |
      | Rust     | {"release": {}} | "ci / passed", "release / release" |
      | service  | {}              | "ci / passed"                      |

  Scenario Outline: sync --check refuses SonarCloud Code Analysis where the merge queue skips the scan
    Given a <language> repository whose ci block is '{}'
    And pubkit.json's protect block is {"checks": ["ci / passed", "SonarCloud Code Analysis"]}
    When I run pubkit "sync"
    And I run pubkit "sync"
    And I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "protect.checks: SonarCloud Code Analysis: no workflow scans with SonarQube Cloud on every merge_group run"
    And stderr mentions "so drop it from protect.checks"

    Examples:
      | language |
      | Python   |
      | Node     |
      | service  |

  Scenario: sync --check takes SonarCloud Code Analysis for a crate, whose queue run always scans
    Given a Rust repository whose ci block is '{}'
    And pubkit.json's protect block is {"checks": ["ci / passed", "SonarCloud Code Analysis"]}
    When I run pubkit "sync"
    And I run pubkit "sync"
    And I run pubkit "sync" "--check"
    Then it prints "in step with pubkit"
    And stderr does not mention "protect.checks"

  Scenario: sync --check takes SonarCloud Code Analysis with no merge queue, where the pull request's run scans
    Given a Python repository whose ci block is '{}'
    And pubkit.json's protect block is {"checks": ["ci / passed", "SonarCloud Code Analysis"], "queue": false}
    When I run pubkit "sync"
    And I run pubkit "sync"
    And I run pubkit "sync" "--check"
    Then it succeeds

  Scenario: sync --check refuses a check no workflow reports
    Given a Python repository whose ci block is '{}'
    And pubkit.json's protect block is {"checks": ["ci / passed", "build"]}
    When I run pubkit "sync"
    Then it fails
    And stderr mentions "protect.checks: build: no workflow that runs on merge_group reports it, so the merge queue would wait for it forever"
    When I run pubkit "sync" "--check"
    Then it fails

  Scenario: sync --check takes a hand-written workflow's check when it runs in the merge queue
    Given a Python repository whose ci block is '{}'
    And pubkit.json's protect block is {"checks": ["ci / passed", "hosting / passed"]}
    And the workflow "hosting.yml" has the job "hosting / passed" on "pull_request, merge_group"
    When I run pubkit "sync"
    And I run pubkit "sync"
    And I run pubkit "sync" "--check"
    Then it succeeds

  Scenario: sync --check refuses a hand-written workflow's check when it does not run in the merge queue
    Given a Python repository whose ci block is '{}'
    And pubkit.json's protect block is {"checks": ["ci / passed", "hosting / passed"]}
    And the workflow "hosting.yml" has the job "hosting / passed" on "pull_request"
    When I run pubkit "sync"
    And I run pubkit "sync"
    And I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "hosting / passed: no workflow that runs on merge_group reports it"

  Scenario: sync --check takes a check under the prefix of a job that calls a reusable workflow
    Given a Python repository whose ci block is '{}'
    And pubkit.json's protect block is {"checks": ["ci / passed", "integrations / scan"]}
    And the workflow "integrations.yml" calls "example-org/integrations/.github/workflows/scan.yml@v1" as "integrations" on "pull_request, merge_group"
    When I run pubkit "sync"
    And I run pubkit "sync"
    And I run pubkit "sync" "--check"
    Then it succeeds

  Scenario: Without runs-on no job is given a runner, and each runs on ubuntu-latest
    Given a Node repository whose ci block is '{}'
    When I run pubkit "sync"
    Then it succeeds
    And no job in "pr.yml" is given a runner, and passed runs on ubuntu-latest
    And no job in "main.yml" is given a runner, and passed runs on ubuntu-latest

  Scenario Outline: A runs-on label runs every job on those runners
    Given a <language> repository whose ci block is '{"runs-on": "my-runners"}'
    When I run pubkit "sync"
    Then it succeeds
    And every job in "pr.yml" but <exempt> runs on "my-runners"
    And every job in "main.yml" but <exempt> runs on "my-runners"

    Examples:
      | language | exempt                |
      | Node     | release               |
      | Python   | release               |
      | Rust     | release and test      |
      | service  | none                  |

  Scenario: runs-on by job places each job, the rest on the default
    Given a Node repository whose ci block is '{"runs-on": {"default": "my-runners", "review": "ubuntu-latest", "passed": "other-runners"}}'
    When I run pubkit "sync"
    Then it succeeds
    And in "pr.yml" the "test" job is given "runs-on" "my-runners"
    And in "pr.yml" the "tested" job is given "runs-on" "my-runners"
    And in "pr.yml" the "review" job is given "runs-on" "ubuntu-latest"
    And in "pr.yml" the "passed" job runs on "other-runners"

  Scenario: runs-on by job without a default leaves the other jobs on ubuntu-latest
    Given a Node repository whose ci block is '{"runs-on": {"test": "my-runners"}}'
    When I run pubkit "sync"
    Then it succeeds
    And in "pr.yml" the "test" job is given "runs-on" "my-runners"
    And in "pr.yml" the "audit" job is given no runner
    And in "pr.yml" the "passed" job runs on "ubuntu-latest"

  Scenario Outline: A runs-on that is not runner labels stops sync, saying what it takes
    Given a <language> repository whose ci block is '{"runs-on": <runs-on>}'
    When I run pubkit "sync"
    Then it fails
    And stderr mentions "<says>"

    Examples:
      | language | runs-on                         | says                                      |
      | Node     | ""                              | runs-on.default must be a runner label    |
      | Node     | 3                               | runs-on must be a runner label            |
      | Node     | {}                              | runs-on must be a runner label            |
      | Node     | ["my-runners"]                  | runs-on must be a runner label            |
      | Node     | "my runners"                    | runs-on.default must be a runner label    |
      | Node     | {"test": 3}                     | runs-on.test must be a runner label       |
      | Node     | {"gates": "my-runners"}         | runs-on names gates; it takes default     |
      | Node     | {"release": "my-runners"}       | runs-on names release; it takes default   |
      | Rust     | {"test": "my-runners"}          | whose runners its os-matrix input gives   |

  Scenario: Every reusable workflow a job calls takes its runner as an input
    Then every job workflow pubkit calls, but Rust's test, takes runs-on, ubuntu-latest by default
    And python-ci.yml, node-ci.yml, rust-ci.yml and service-ci.yml take runs-on, ubuntu-latest by default
