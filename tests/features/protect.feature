Feature: Every repository carries pubkit's rulesets, on main and on its release tags
  src/pubkit/ruleset.json is main's ruleset, and tag-ruleset.json the
  release tags', both shipped in the wheel. Which repositories carry it comes from a
  repos file or the command line, and the checks each requires from its own pubkit.json,
  or --require. `pubkit protect` creates or
  updates the rulesets through the GitHub API, and `pubkit protect --check`
  reports how a repository differs, changing nothing.

  Scenario: The ruleset keeps main to pull requests that passed their checks
    When I build the ruleset requiring "ci / passed, SonarCloud Code Analysis"
    Then it targets the default branch and nobody may bypass it
    And it blocks deleting and force-pushing the branch
    And it takes changes only by pull request, with no approval needed
    And it requires "ci / passed" from GitHub Actions and "SonarCloud Code Analysis" from SonarCloud
    And it merges through the merge queue, by merge commit, without asking the branch to be up to date

  Scenario: Without the merge queue, the ruleset requires a branch up to date with main
    When I build the ruleset requiring "ci / passed", without the merge queue
    Then it has no merge queue
    And it requires a branch to be up to date with main before it merges

  Scenario: The tag ruleset leaves release tags to CI alone
    When I build the tag ruleset
    Then it targets the tags "refs/tags/v*"
    And it blocks creating, moving and deleting them
    And only GitHub Actions may bypass it

  Scenario: A repository's protect block names the release app its tags let through
    Given the working directory is a clone of "example-org/site" whose pubkit.json requires "ci / passed"
    And its pubkit.json's protect block is {"checks": ["ci / passed"], "release_app": 12345}
    And GitHub has no ruleset in "example-org/site"
    When I run pubkit "protect"
    Then it succeeds
    And it prints nothing about a release app
    And GitHub was sent a tag ruleset only app 12345 may bypass

  Scenario: A repository naming no release app gets no app and no warning
    Given GitHub has no ruleset in "example-org/foo"
    When I run pubkit "protect" "--require" "ci / passed" "example-org/foo"
    Then it succeeds
    And it prints nothing about a release app
    And GitHub was sent the tag ruleset for "example-org/foo"

  Scenario Outline: sync --check refuses an unsound protect block
    Given its pubkit.json's protect block is <block>
    When I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "<reason>"

    Examples:
      | block                                        | reason                                  |
      | {"checks": ["build"], "release_app": "1"}    | "release_app" must be a GitHub App's id |
      | {"checks": ["build"], "bypass": 1}           | bypass is not a protect setting         |

  Scenario: protect creates the ruleset where there is none
    Given "example-org/foo"'s pubkit.json on GitHub requires "ci / passed, release / release"
    And GitHub has no ruleset in "example-org/foo"
    When I run pubkit "protect" "example-org/foo"
    Then it succeeds
    And it prints "example-org/foo: created "main changes by pull request, after CI""
    And it prints "example-org/foo: created "release tags only from CI""
    And GitHub was sent the ruleset for "example-org/foo" requiring "ci / passed, release / release"
    And GitHub was sent the tag ruleset for "example-org/foo"

  Scenario: protect --check reports a ruleset that differs, and changes nothing
    Given "example-org/foo"'s pubkit.json on GitHub requires "ci / passed"
    And GitHub has the ruleset "main" in "example-org/foo" requiring "test"
    When I run pubkit "protect" "--check" "example-org/foo"
    Then it fails
    And it prints "does not require ci / passed (app 15368)"
    And it prints "also requires test (app 15368)"
    And it prints "name is "main""
    And GitHub was sent nothing

  Scenario: protect updates the ruleset already on main, and renames it
    Given "example-org/foo"'s pubkit.json on GitHub requires "ci / passed"
    And GitHub has the ruleset "main" in "example-org/foo" requiring "test"
    When I run pubkit "protect" "example-org/foo"
    Then it succeeds
    And it prints "example-org/foo: updated "main""
    And GitHub was sent the ruleset for "example-org/foo" requiring "ci / passed", replacing "main"
    And it prints "example-org/foo: "release tags only from CI" up to date"

  Scenario: A ruleset that matches is up to date, whatever GitHub adds to it
    Given "example-org/foo"'s pubkit.json on GitHub requires "ci / passed"
    And GitHub has pubkit's ruleset in "example-org/foo" requiring "ci / passed"
    When I run pubkit "protect" "--check" "example-org/foo"
    Then it succeeds
    And it prints "example-org/foo: "main changes by pull request, after CI" up to date"
    And it prints "example-org/foo: "release tags only from CI" up to date"

  Scenario: protect --check reports release tags without pubkit's tag ruleset
    Given "example-org/foo"'s pubkit.json on GitHub requires "ci / passed"
    And GitHub has only main's ruleset in "example-org/foo" requiring "ci / passed"
    When I run pubkit "protect" "--check" "example-org/foo"
    Then it fails
    And it prints "example-org/foo: no ruleset on its release tags"
    And GitHub was sent nothing

  Scenario: A repository carrying no protect block is not protected
    Given GitHub has no ruleset in "example-org/bar"
    When I run pubkit "protect" "example-org/bar"
    Then it fails
    And it prints "example-org/bar: no pubkit.json on its default branch"
    And GitHub was sent nothing

  Scenario: The named repositories' own pubkit.json are read
    Given "example-org/site"'s pubkit.json on GitHub requires "ci / passed, build"
    And GitHub has no ruleset in "example-org/site"
    When I run pubkit "protect" "example-org/site"
    Then it succeeds
    And GitHub was sent the ruleset for "example-org/site" requiring "ci / passed, build"
    And GitHub was sent the tag ruleset for "example-org/site"

  Scenario: Inside a repository, with nothing named, protect reads ./pubkit.json
    Given the working directory is a clone of "example-org/site" whose pubkit.json requires "ci / passed", without tags
    And GitHub has no ruleset in "example-org/site"
    When I run pubkit "protect"
    Then it succeeds
    And it prints "example-org/site: created "main changes by pull request, after CI""
    And GitHub was sent no tag ruleset

  Scenario: A pubkit.json without a protect block says what to add
    Given the working directory is a clone of "example-org/site" whose pubkit.json requires "ci / passed"
    And its pubkit.json has no protect block
    When I run pubkit "protect"
    Then it fails
    And stderr mentions "./pubkit.json has no protect block"

  Scenario: protect says who may change a ruleset when GitHub refuses
    Given "example-org/foo"'s pubkit.json on GitHub requires "ci / passed"
    And GitHub refuses to change rulesets in "example-org/foo"
    When I run pubkit "protect" "example-org/foo"
    Then it fails
    And it prints "needs a repository admin"

  Scenario: With nothing named and no ./pubkit.json, protect says what to give it
    When I run pubkit "protect"
    Then it fails
    And stderr mentions "pass --repos FILE, or name the repositories with --require"

  Scenario: A repository whose block has "tags": false gets only main's ruleset
    Given "example-org/site"'s pubkit.json on GitHub requires "ci / passed", without tags
    And GitHub has no ruleset in "example-org/site"
    When I run pubkit "protect" "example-org/site"
    Then it succeeds
    And it prints "example-org/site: created "main changes by pull request, after CI""
    And GitHub was sent the ruleset for "example-org/site" requiring "ci / passed"
    And GitHub was sent no tag ruleset

  Scenario: A repository whose block has "queue": false gets main's ruleset without the merge queue
    Given "example-org/site"'s pubkit.json on GitHub requires "ci / passed", without the merge queue
    And GitHub has no ruleset in "example-org/site"
    When I run pubkit "protect" "example-org/site"
    Then it succeeds
    And GitHub was sent the ruleset for "example-org/site" requiring "ci / passed", without the merge queue

  Scenario: --no-queue leaves out the merge queue
    Given GitHub has no ruleset in "example-org/baz"
    When I run pubkit "protect" "--no-queue" "--require" "ci / passed" "example-org/baz"
    Then it succeeds
    And GitHub was sent the ruleset for "example-org/baz" requiring "ci / passed", without the merge queue

  Scenario: A private repository off Enterprise Cloud gets main's ruleset without the merge queue
    Given "example-org/private" is a private repository of an organization on the "team" plan
    And GitHub has no ruleset in "example-org/private"
    When I run pubkit "protect" "--require" "ci / passed" "example-org/private"
    Then it succeeds
    And it prints "example-org/private: no merge queue: GitHub offers a merge queue to a private repository only on Enterprise Cloud, and example-org is on team"
    And it prints "main merges only after its required checks pass on a branch up to date with main"
    And GitHub was sent the ruleset for "example-org/private" requiring "ci / passed", without the merge queue

  Scenario: A private repository on Enterprise Cloud keeps the merge queue
    Given "example-org/private" is a private repository of an organization on the "enterprise" plan
    And GitHub has no ruleset in "example-org/private"
    When I run pubkit "protect" "--require" "ci / passed" "example-org/private"
    Then it succeeds
    And it prints nothing about a merge queue
    And GitHub was sent the ruleset for "example-org/private" requiring "ci / passed"

  Scenario: A personal account's repository gets main's ruleset without the merge queue
    Given "example-org/example-site" is a public repository of a personal account
    And GitHub has no ruleset in "example-org/example-site"
    When I run pubkit "protect" "--require" "ci / passed" "example-org/example-site"
    Then it succeeds
    And it prints "example-org/example-site: no merge queue: GitHub offers a merge queue only to an organization's repositories"
    And GitHub was sent the ruleset for "example-org/example-site" requiring "ci / passed", without the merge queue

  Scenario: Where GitHub still refuses the merge queue, protect tries once more without it
    Given GitHub refuses the merge queue in "example-org/foo"
    And GitHub has no ruleset in "example-org/foo"
    When I run pubkit "protect" "--require" "ci / passed" "example-org/foo"
    Then it succeeds
    And it prints "example-org/foo: warning: GitHub refused the merge_queue rule, so main goes without the queue"
    And GitHub was sent the ruleset for "example-org/foo" requiring "ci / passed", without the merge queue

  Scenario: protect --check takes a private repository's ruleset without the merge queue as up to date
    Given "example-org/private" is a private repository of an organization on the "team" plan
    And GitHub has pubkit's ruleset in "example-org/private" requiring "ci / passed", without the merge queue
    When I run pubkit "protect" "--require" "ci / passed" "--no-tags" "--check" "example-org/private"
    Then it succeeds
    And it prints "example-org/private: "main changes by pull request, after CI" up to date"
    And GitHub was sent nothing

  Scenario: protect --check reports a ruleset without the merge queue
    Given "example-org/foo"'s pubkit.json on GitHub requires "ci / passed"
    And GitHub has pubkit's ruleset in "example-org/foo" requiring "ci / passed", without the merge queue
    When I run pubkit "protect" "--check" "example-org/foo"
    Then it fails
    And it prints "no merge_queue rule"
    And it prints "required_status_checks.strict_required_status_checks_policy is true, not false"
    And GitHub was sent nothing

  Scenario: A repository's own pubkit.json can leave out the merge queue
    Given the working directory is a clone of "example-org/site" whose pubkit.json requires "ci / passed"
    And its pubkit.json's protect block is {"checks": ["ci / passed"], "queue": false}
    And GitHub has no ruleset in "example-org/site"
    When I run pubkit "protect"
    Then it succeeds
    And GitHub was sent the ruleset for "example-org/site" requiring "ci / passed", without the merge queue

  Scenario: A repository whose block has "auto_merge": true gets auto-merge allowed
    Given "example-org/site"'s pubkit.json on GitHub requires "ci / passed", with auto-merge
    And GitHub has only main's ruleset in "example-org/site" requiring "ci / passed"
    And "example-org/site" does not allow auto-merge
    When I run pubkit "protect" "example-org/site"
    Then it succeeds
    And it prints "example-org/site: auto-merge now allowed"
    And GitHub was asked to allow auto-merge in "example-org/site"

  Scenario: protect --check reports auto-merge left off, and changes nothing
    Given "example-org/site"'s pubkit.json on GitHub requires "ci / passed", with auto-merge
    And GitHub has only main's ruleset in "example-org/site" requiring "ci / passed"
    And "example-org/site" does not allow auto-merge
    When I run pubkit "protect" "--check" "example-org/site"
    Then it fails
    And it prints "example-org/site: auto-merge is not allowed; pubkit wants it allowed"
    And GitHub was sent nothing

  Scenario: Auto-merge already allowed is up to date
    Given "example-org/site"'s pubkit.json on GitHub requires "ci / passed", with auto-merge
    And GitHub has only main's ruleset in "example-org/site" requiring "ci / passed"
    And "example-org/site" allows auto-merge
    When I run pubkit "protect" "--check" "example-org/site"
    Then it succeeds
    And it prints "example-org/site: auto-merge allowed, up to date"

  Scenario: Without auto_merge, protect leaves the setting alone
    Given "example-org/site"'s pubkit.json on GitHub requires "ci / passed", without tags
    And GitHub has only main's ruleset in "example-org/site" requiring "ci / passed"
    When I run pubkit "protect" "example-org/site"
    Then it succeeds
    And it prints nothing about auto-merge
    And GitHub was sent nothing

  Scenario: A repository's own pubkit.json can allow auto-merge
    Given the working directory is a clone of "example-org/site" whose pubkit.json requires "ci / passed"
    And its pubkit.json's protect block is {"checks": ["ci / passed"], "tags": false, "auto_merge": true}
    And GitHub has only main's ruleset in "example-org/site" requiring "ci / passed"
    And "example-org/site" does not allow auto-merge
    When I run pubkit "protect"
    Then it succeeds
    And GitHub was asked to allow auto-merge in "example-org/site"

  Scenario: --no-tags leaves out the tag ruleset
    Given GitHub has no ruleset in "example-org/baz"
    When I run pubkit "protect" "--no-tags" "--require" "ci / passed" "example-org/baz"
    Then it succeeds
    And GitHub was sent the ruleset for "example-org/baz" requiring "ci / passed"
    And GitHub was sent no tag ruleset

  Scenario: When GitHub rejects only the tag ruleset, main's is still reported
    Given GitHub has no ruleset in "example-org/foo"
    And GitHub rejects the tag ruleset for "example-org/foo" because "Actor GitHub Actions integration must be part of the ruleset source or owner organization"
    When I run pubkit "protect" "--require" "ci / passed" "example-org/foo"
    Then it fails
    And it prints "example-org/foo: created "main changes by pull request, after CI""
    And it prints "Actor GitHub Actions integration must be part of the ruleset source"

  Scenario: protect shows why GitHub rejected a ruleset
    Given "example-org/foo"'s pubkit.json on GitHub requires "ci / passed, SonarCloud Code Analysis"
    And GitHub rejects the ruleset for "example-org/foo" because "Invalid rule 'required_status_checks': integration 12526 is not installed"
    When I run pubkit "protect" "example-org/foo"
    Then it fails
    And it prints "Validation Failed (HTTP 422); Invalid rule 'required_status_checks': integration 12526 is not installed"
    And it prints nothing about a repository admin

  Scenario: --require protects one repository with the checks it names, without a list
    Given GitHub has no ruleset in "example-org/baz"
    When I run pubkit "protect" "--require" "ci / passed" "--require" "build" "example-org/baz"
    Then it succeeds
    And GitHub was sent the ruleset for "example-org/baz" requiring "ci / passed, build"

  Scenario: --require needs the repositories it applies to
    When I run pubkit "protect" "--require" "ci / passed"
    Then it fails
    And stderr mentions "name the repositories too"

  Scenario Outline: A protect block that is not sound is refused before GitHub is asked
    Given the working directory is a clone of "example-org/site" whose pubkit.json requires "ci / passed"
    And its pubkit.json's protect block is <block>
    When I run pubkit "protect"
    Then it fails
    And stderr mentions "<reason>"
    And GitHub was sent nothing

    Examples:
      | block                                     | reason                                    |
      | {"checks": []}                            | "checks" must list at least one check     |
      | {"checks": "ci / passed"}                 | "checks" must list at least one check     |
      | {"checks": ["ci / passed", ""]}           | every check must be a name                |
      | {"checks": [" ci / passed"]}              | without surrounding spaces                |
      | {"checks": ["build", "build"]}            | "checks" lists a check twice              |
      | {"checks": ["build"], "tags": "no"}       | "tags" must be true or false, not "no"    |
      | {"checks": ["build"], "queue": 1}         | "queue" must be true or false, not 1      |
      | {"checks": ["build"], "tag": false}       | tag is not a protect setting              |
      | {"checks": ["build"], "release_app": "1"} | "release_app" must be a GitHub App's id   |
      | {"checks": ["build"], "release_app": true} | "release_app" must be a GitHub App's id  |
      | {"checks": ["build"], "release_app": 0}   | "release_app" must be a GitHub App's id   |
      | {"checks": ["build"], "release_app": 1.5} | "release_app" must be a GitHub App's id   |
      | ["build"]                                 | it is ["build"], not an object            |

  Scenario: A ./pubkit.json that is not JSON is refused
    Given the working directory is a clone of "example-org/site" whose pubkit.json requires "ci / passed"
    And its pubkit.json is "{not json"
    When I run pubkit "protect"
    Then it fails
    And stderr mentions "./pubkit.json is not JSON"

  Scenario: A repository's pubkit.json on GitHub that is not JSON is reported, and nothing sent
    Given "example-org/site"'s pubkit.json on GitHub is "{not json"
    When I run pubkit "protect" "example-org/site"
    Then it fails
    And it prints "example-org/site: its pubkit.json is not JSON"
    And GitHub was sent nothing

  Scenario: A repository's unsound protect block on GitHub is reported, and nothing sent
    Given "example-org/site"'s pubkit.json on GitHub is "{"protect": {"checks": ["build"], "tags": 0}}"
    When I run pubkit "protect" "example-org/site"
    Then it fails
    And it prints "its pubkit.json's protect block: "tags" must be true or false, not 0"
    And GitHub was sent nothing

  Scenario: protect refuses a named repository that is not OWNER/NAME
    When I run pubkit "protect" "example-org/foo/../bar"
    Then it fails
    And stderr mentions "example-org/foo/../bar is not OWNER/NAME"

  Scenario: A read-only token can't see who may bypass a ruleset, and that's no difference
    Given pubkit's ruleset as a read-only token sees it, with no bypass_actors
    When I compare it with pubkit's ruleset
    Then it finds no difference

  Scenario: A bypass actor that can be read still counts
    Given pubkit's ruleset, with a bypass actor added
    When I compare it with pubkit's ruleset
    Then it reports the bypass actors

  Scenario: A public repository gets secret scanning and push protection turned on
    Given "example-org/site"'s pubkit.json on GitHub requires "ci / passed"
    And GitHub has no ruleset in "example-org/site"
    And "example-org/site" has secret scanning "disabled" and push protection "disabled"
    When I run pubkit "protect" "example-org/site"
    Then it succeeds
    And it prints "example-org/site: turned on secret scanning, push protection"
    And GitHub was asked to turn on "secret_scanning, secret_scanning_push_protection" in "example-org/site"

  Scenario: Only what's off is turned on
    Given "example-org/site"'s pubkit.json on GitHub requires "ci / passed"
    And GitHub has no ruleset in "example-org/site"
    And "example-org/site" has secret scanning "enabled" and push protection "disabled"
    When I run pubkit "protect" "example-org/site"
    Then it succeeds
    And GitHub was asked to turn on "secret_scanning_push_protection" in "example-org/site"

  Scenario: protect --check reports them off, and changes nothing
    Given "example-org/site"'s pubkit.json on GitHub requires "ci / passed"
    And GitHub has pubkit's ruleset in "example-org/site" requiring "ci / passed"
    And "example-org/site" has secret scanning "disabled" and push protection "disabled"
    When I run pubkit "protect" "--check" "--no-tags" "example-org/site"
    Then the exit code is 1
    And it prints "pubkit wants it on"
    And GitHub was asked to turn on nothing in "example-org/site"

  Scenario: Already on, nothing is asked
    Given "example-org/site"'s pubkit.json on GitHub requires "ci / passed"
    And GitHub has no ruleset in "example-org/site"
    And "example-org/site" has secret scanning "enabled" and push protection "enabled"
    When I run pubkit "protect" "example-org/site"
    Then it prints "secret scanning and push protection on, up to date"
    And GitHub was asked to turn on nothing in "example-org/site"

  Scenario: A private repository is left alone
    Given "example-org/site"'s pubkit.json on GitHub requires "ci / passed"
    And GitHub has no ruleset in "example-org/site"
    And "example-org/site" is a private repository of an organization on the "team" plan
    When I run pubkit "protect" "example-org/site"
    Then it prints "secret scanning left alone"
    And GitHub was asked to turn on nothing in "example-org/site"

  Scenario: A token that can't read them changes nothing, and fails nothing
    Given "example-org/site"'s pubkit.json on GitHub requires "ci / passed"
    And GitHub has pubkit's ruleset in "example-org/site" requiring "ci / passed"
    When I run pubkit "protect" "--check" "--no-tags" "example-org/site"
    Then it succeeds
    And it prints "secret scanning not readable"
    And GitHub was asked to turn on nothing in "example-org/site"

  Scenario: A block can opt out
    Given "example-org/site"'s pubkit.json on GitHub says secret_scanning is false, requiring "ci / passed"
    And GitHub has no ruleset in "example-org/site"
    And "example-org/site" has secret scanning "disabled" and push protection "disabled"
    When I run pubkit "protect" "example-org/site"
    Then GitHub was asked to turn on nothing in "example-org/site"
