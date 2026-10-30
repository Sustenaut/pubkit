Feature: Every repository carries pubkit's rulesets, on main and on its release tags
  src/pubkit/ruleset.json is main's ruleset, and tag-ruleset.json the
  release tags', both shipped in the wheel. Which
  repositories carry it, with the checks each requires, is local: a
  protected.json (pubkit's own sits at its root), or --require. `pubkit protect` creates or
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

  Scenario: A Sustenaut repository's release tags let the release app through instead
    When I build the tag ruleset for "Sustenaut/pubthing"
    Then it targets the tags "refs/tags/v*"
    And it blocks creating, moving and deleting them
    And only the release app may bypass it

  Scenario: protect gives a Sustenaut repository the release app's tag ruleset
    Given GitHub has no ruleset in "Sustenaut/foo"
    When I run pubkit "protect" "--require" "ci / passed" "Sustenaut/foo"
    Then it succeeds
    And it prints "Sustenaut/foo: warning: no release_app in its protect block"
    And GitHub was sent the tag ruleset for "Sustenaut/foo"

  Scenario: A repository's protect block names the release app its tags let through
    Given the working directory is a clone of "schryer/site" whose pubkit.json requires "ci / passed"
    And its pubkit.json's protect block is {"checks": ["ci / passed"], "release_app": 12345}
    And GitHub has no ruleset in "schryer/site"
    When I run pubkit "protect"
    Then it succeeds
    And it prints nothing about a release app
    And GitHub was sent a tag ruleset only app 12345 may bypass

  Scenario: A release app in a protected.json entry is the one its tags let through
    Given protected.json lists "Sustenaut/foo" requiring "ci / passed", with the release app 12345
    And GitHub has no ruleset in "Sustenaut/foo"
    When I run pubkit "protect" "--config" "protected.json"
    Then it succeeds
    And it prints nothing about a release app
    And GitHub was sent a tag ruleset only app 12345 may bypass

  Scenario: A repository of no known owner, naming no release app, gets no app and no warning
    Given GitHub has no ruleset in "example-org/foo"
    When I run pubkit "protect" "--require" "ci / passed" "example-org/foo"
    Then it succeeds
    And it prints nothing about a release app
    And GitHub was sent the tag ruleset for "example-org/foo"

  Scenario: A release tag ruleset the block leaves out gives no warning about its app
    Given GitHub has no ruleset in "Sustenaut/foo"
    When I run pubkit "protect" "--require" "ci / passed" "--no-tags" "Sustenaut/foo"
    Then it succeeds
    And it prints nothing about a release app

  Scenario Outline: sync --check refuses an unsound protect block
    Given its pubkit.json's protect block is <block>
    When I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "<reason>"

    Examples:
      | block                                        | reason                                  |
      | {"checks": ["build"], "release_app": "1"}    | "release_app" must be a GitHub App's id |
      | {"checks": ["build"], "bypass": 1}           | bypass is not a protect setting         |

  Scenario: protected.json covers every governed repository
    When I read pubkit's protected.json
    Then every repository in it requires at least one check, and is listed once
    And every repository in governed.json is in it

  Scenario: protect creates the ruleset where there is none
    Given protected.json lists "schryer/foo" requiring "ci / passed, release / release"
    And GitHub has no ruleset in "schryer/foo"
    When I run pubkit "protect" "--config" "protected.json"
    Then it succeeds
    And it prints "schryer/foo: created "main changes by pull request, after CI""
    And it prints "schryer/foo: created "release tags only from CI""
    And GitHub was sent the ruleset for "schryer/foo" requiring "ci / passed, release / release"
    And GitHub was sent the tag ruleset for "schryer/foo"

  Scenario: protect --check reports a ruleset that differs, and changes nothing
    Given protected.json lists "schryer/foo" requiring "ci / passed"
    And GitHub has the ruleset "main" in "schryer/foo" requiring "test"
    When I run pubkit "protect" "--config" "protected.json" "--check"
    Then it fails
    And it prints "does not require ci / passed (app 15368)"
    And it prints "also requires test (app 15368)"
    And it prints "name is "main""
    And GitHub was sent nothing

  Scenario: protect updates the ruleset already on main, and renames it
    Given protected.json lists "schryer/foo" requiring "ci / passed"
    And GitHub has the ruleset "main" in "schryer/foo" requiring "test"
    When I run pubkit "protect" "--config" "protected.json" "schryer/foo"
    Then it succeeds
    And it prints "schryer/foo: updated "main""
    And GitHub was sent the ruleset for "schryer/foo" requiring "ci / passed", replacing "main"
    And it prints "schryer/foo: "release tags only from CI" up to date"

  Scenario: A ruleset that matches is up to date, whatever GitHub adds to it
    Given protected.json lists "schryer/foo" requiring "ci / passed"
    And GitHub has pubkit's ruleset in "schryer/foo" requiring "ci / passed"
    When I run pubkit "protect" "--config" "protected.json" "--check"
    Then it succeeds
    And it prints "schryer/foo: "main changes by pull request, after CI" up to date"
    And it prints "schryer/foo: "release tags only from CI" up to date"

  Scenario: protect --check reports release tags without pubkit's tag ruleset
    Given protected.json lists "schryer/foo" requiring "ci / passed"
    And GitHub has only main's ruleset in "schryer/foo" requiring "ci / passed"
    When I run pubkit "protect" "--config" "protected.json" "--check"
    Then it fails
    And it prints "schryer/foo: no ruleset on its release tags"
    And GitHub was sent nothing

  Scenario: A repository neither listed nor carrying a protect block is not protected
    Given protected.json lists "schryer/foo" requiring "ci / passed"
    When I run pubkit "protect" "--config" "protected.json" "schryer/bar"
    Then it fails
    And it prints "schryer/bar: not in protected.json, and no pubkit.json on its default branch"
    And GitHub was sent nothing

  Scenario: A repository protected.json lacks is protected as its own pubkit.json says
    Given protected.json lists "schryer/foo" requiring "ci / passed"
    And "schryer/site"'s pubkit.json on GitHub requires "ci / passed", without tags
    And GitHub has no ruleset in "schryer/site"
    When I run pubkit "protect" "schryer/site"
    Then it succeeds
    And GitHub was sent the ruleset for "schryer/site" requiring "ci / passed"
    And GitHub was sent no tag ruleset

  Scenario: Without any list, the named repositories' own pubkit.json are read
    Given "schryer/site"'s pubkit.json on GitHub requires "ci / passed, build"
    And GitHub has no ruleset in "schryer/site"
    When I run pubkit "protect" "schryer/site"
    Then it succeeds
    And GitHub was sent the ruleset for "schryer/site" requiring "ci / passed, build"
    And GitHub was sent the tag ruleset for "schryer/site"

  Scenario: Inside a repository, with nothing named, protect reads ./pubkit.json
    Given the working directory is a clone of "schryer/site" whose pubkit.json requires "ci / passed", without tags
    And GitHub has no ruleset in "schryer/site"
    When I run pubkit "protect"
    Then it succeeds
    And it prints "schryer/site: created "main changes by pull request, after CI""
    And GitHub was sent no tag ruleset

  Scenario: A pubkit.json without a protect block says what to add
    Given the working directory is a clone of "schryer/site" whose pubkit.json requires "ci / passed"
    And its pubkit.json has no protect block
    When I run pubkit "protect"
    Then it fails
    And stderr mentions "./pubkit.json has no protect block"

  Scenario: protect says who may change a ruleset when GitHub refuses
    Given protected.json lists "schryer/foo" requiring "ci / passed"
    And GitHub refuses to change rulesets in "schryer/foo"
    When I run pubkit "protect" "--config" "protected.json"
    Then it fails
    And it prints "needs a repository admin"

  Scenario: protect refuses an entry that is not a repository
    Given protected.json lists "schryer/foo/../bar" requiring "ci / passed"
    When I run pubkit "protect" "--config" "protected.json" "--check"
    Then it fails
    And stderr mentions "needs a repo, OWNER/NAME"

  Scenario: Without a list or --require, protect says how to give one
    When I run pubkit "protect"
    Then it fails
    And stderr mentions "pass --config FILE, or name the repositories with --require"

  Scenario: A repository listed with "tags": false gets only main's ruleset
    Given protected.json lists "schryer/site" requiring "ci / passed", without tags
    And GitHub has no ruleset in "schryer/site"
    When I run pubkit "protect" "--config" "protected.json"
    Then it succeeds
    And it prints "schryer/site: created "main changes by pull request, after CI""
    And GitHub was sent the ruleset for "schryer/site" requiring "ci / passed"
    And GitHub was sent no tag ruleset

  Scenario: A repository listed with "queue": false gets main's ruleset without the merge queue
    Given protected.json lists "schryer/site" requiring "ci / passed", without the merge queue
    And GitHub has no ruleset in "schryer/site"
    When I run pubkit "protect" "--config" "protected.json"
    Then it succeeds
    And GitHub was sent the ruleset for "schryer/site" requiring "ci / passed", without the merge queue

  Scenario: --no-queue leaves out the merge queue
    Given GitHub has no ruleset in "schryer/baz"
    When I run pubkit "protect" "--no-queue" "--require" "ci / passed" "schryer/baz"
    Then it succeeds
    And GitHub was sent the ruleset for "schryer/baz" requiring "ci / passed", without the merge queue

  Scenario: A private repository off Enterprise Cloud gets main's ruleset without the merge queue
    Given "Sustenaut/storage" is a private repository of an organization on the "team" plan
    And GitHub has no ruleset in "Sustenaut/storage"
    When I run pubkit "protect" "--require" "ci / passed" "Sustenaut/storage"
    Then it succeeds
    And it prints "Sustenaut/storage: no merge queue: GitHub offers a merge queue to a private repository only on Enterprise Cloud, and Sustenaut is on team"
    And it prints "main merges only after its required checks pass on a branch up to date with main"
    And GitHub was sent the ruleset for "Sustenaut/storage" requiring "ci / passed", without the merge queue

  Scenario: A private repository on Enterprise Cloud keeps the merge queue
    Given "Sustenaut/storage" is a private repository of an organization on the "enterprise" plan
    And GitHub has no ruleset in "Sustenaut/storage"
    When I run pubkit "protect" "--require" "ci / passed" "Sustenaut/storage"
    Then it succeeds
    And it prints nothing about a merge queue
    And GitHub was sent the ruleset for "Sustenaut/storage" requiring "ci / passed"

  Scenario: A personal account's repository gets main's ruleset without the merge queue
    Given "schryer/recipes" is a public repository of a personal account
    And GitHub has no ruleset in "schryer/recipes"
    When I run pubkit "protect" "--require" "ci / passed" "schryer/recipes"
    Then it succeeds
    And it prints "schryer/recipes: no merge queue: GitHub offers a merge queue only to an organization's repositories"
    And GitHub was sent the ruleset for "schryer/recipes" requiring "ci / passed", without the merge queue

  Scenario: Where GitHub still refuses the merge queue, protect tries once more without it
    Given GitHub refuses the merge queue in "Sustenaut/foo"
    And GitHub has no ruleset in "Sustenaut/foo"
    When I run pubkit "protect" "--require" "ci / passed" "Sustenaut/foo"
    Then it succeeds
    And it prints "Sustenaut/foo: warning: GitHub refused the merge_queue rule, so main goes without the queue"
    And GitHub was sent the ruleset for "Sustenaut/foo" requiring "ci / passed", without the merge queue

  Scenario: protect --check takes a private repository's ruleset without the merge queue as up to date
    Given "Sustenaut/storage" is a private repository of an organization on the "team" plan
    And GitHub has pubkit's ruleset in "Sustenaut/storage" requiring "ci / passed", without the merge queue
    When I run pubkit "protect" "--require" "ci / passed" "--no-tags" "--check" "Sustenaut/storage"
    Then it succeeds
    And it prints "Sustenaut/storage: "main changes by pull request, after CI" up to date"
    And GitHub was sent nothing

  Scenario: protect --check reports a ruleset without the merge queue
    Given protected.json lists "schryer/foo" requiring "ci / passed"
    And GitHub has pubkit's ruleset in "schryer/foo" requiring "ci / passed", without the merge queue
    When I run pubkit "protect" "--config" "protected.json" "--check"
    Then it fails
    And it prints "no merge_queue rule"
    And it prints "required_status_checks.strict_required_status_checks_policy is true, not false"
    And GitHub was sent nothing

  Scenario: A repository's own pubkit.json can leave out the merge queue
    Given the working directory is a clone of "schryer/site" whose pubkit.json requires "ci / passed"
    And its pubkit.json's protect block is {"checks": ["ci / passed"], "queue": false}
    And GitHub has no ruleset in "schryer/site"
    When I run pubkit "protect"
    Then it succeeds
    And GitHub was sent the ruleset for "schryer/site" requiring "ci / passed", without the merge queue

  Scenario: A repository listed with "auto_merge": true gets auto-merge allowed
    Given protected.json lists "schryer/site" requiring "ci / passed", with auto-merge
    And GitHub has only main's ruleset in "schryer/site" requiring "ci / passed"
    And "schryer/site" does not allow auto-merge
    When I run pubkit "protect" "--config" "protected.json"
    Then it succeeds
    And it prints "schryer/site: auto-merge now allowed"
    And GitHub was asked to allow auto-merge in "schryer/site"

  Scenario: protect --check reports auto-merge left off, and changes nothing
    Given protected.json lists "schryer/site" requiring "ci / passed", with auto-merge
    And GitHub has only main's ruleset in "schryer/site" requiring "ci / passed"
    And "schryer/site" does not allow auto-merge
    When I run pubkit "protect" "--config" "protected.json" "--check"
    Then it fails
    And it prints "schryer/site: auto-merge is not allowed; pubkit wants it allowed"
    And GitHub was sent nothing

  Scenario: Auto-merge already allowed is up to date
    Given protected.json lists "schryer/site" requiring "ci / passed", with auto-merge
    And GitHub has only main's ruleset in "schryer/site" requiring "ci / passed"
    And "schryer/site" allows auto-merge
    When I run pubkit "protect" "--config" "protected.json" "--check"
    Then it succeeds
    And it prints "schryer/site: auto-merge allowed, up to date"

  Scenario: Without auto_merge, protect leaves the setting alone
    Given protected.json lists "schryer/site" requiring "ci / passed", without tags
    And GitHub has only main's ruleset in "schryer/site" requiring "ci / passed"
    When I run pubkit "protect" "--config" "protected.json"
    Then it succeeds
    And it prints nothing about auto-merge
    And GitHub was sent nothing

  Scenario: A repository's own pubkit.json can allow auto-merge
    Given the working directory is a clone of "schryer/site" whose pubkit.json requires "ci / passed"
    And its pubkit.json's protect block is {"checks": ["ci / passed"], "tags": false, "auto_merge": true}
    And GitHub has only main's ruleset in "schryer/site" requiring "ci / passed"
    And "schryer/site" does not allow auto-merge
    When I run pubkit "protect"
    Then it succeeds
    And GitHub was asked to allow auto-merge in "schryer/site"

  Scenario: --no-tags leaves out the tag ruleset
    Given GitHub has no ruleset in "schryer/baz"
    When I run pubkit "protect" "--no-tags" "--require" "ci / passed" "schryer/baz"
    Then it succeeds
    And GitHub was sent the ruleset for "schryer/baz" requiring "ci / passed"
    And GitHub was sent no tag ruleset

  Scenario: When GitHub rejects only the tag ruleset, main's is still reported
    Given GitHub has no ruleset in "schryer/foo"
    And GitHub rejects the tag ruleset for "schryer/foo" because "Actor GitHub Actions integration must be part of the ruleset source or owner organization"
    When I run pubkit "protect" "--require" "ci / passed" "schryer/foo"
    Then it fails
    And it prints "schryer/foo: created "main changes by pull request, after CI""
    And it prints "Actor GitHub Actions integration must be part of the ruleset source"

  Scenario: protect shows why GitHub rejected a ruleset
    Given protected.json lists "schryer/foo" requiring "ci / passed, SonarCloud Code Analysis"
    And GitHub rejects the ruleset for "schryer/foo" because "Invalid rule 'required_status_checks': integration 12526 is not installed"
    When I run pubkit "protect" "--config" "protected.json"
    Then it fails
    And it prints "Validation Failed (HTTP 422); Invalid rule 'required_status_checks': integration 12526 is not installed"
    And it prints nothing about a repository admin

  Scenario: Without --config, a protected.json in the working directory is the list
    Given protected.json lists "schryer/foo" requiring "ci / passed"
    And GitHub has no ruleset in "schryer/foo"
    When I run pubkit "protect"
    Then it succeeds
    And it prints "schryer/foo: created"

  Scenario: --require protects one repository with the checks it names, without a list
    Given GitHub has no ruleset in "schryer/baz"
    When I run pubkit "protect" "--require" "ci / passed" "--require" "build" "schryer/baz"
    Then it succeeds
    And GitHub was sent the ruleset for "schryer/baz" requiring "ci / passed, build"

  Scenario: --require needs the repositories it applies to
    When I run pubkit "protect" "--require" "ci / passed"
    Then it fails
    And stderr mentions "name the repositories too"

  Scenario Outline: A protect block that is not sound is refused before GitHub is asked
    Given the working directory is a clone of "schryer/site" whose pubkit.json requires "ci / passed"
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
    Given the working directory is a clone of "schryer/site" whose pubkit.json requires "ci / passed"
    And its pubkit.json is "{not json"
    When I run pubkit "protect"
    Then it fails
    And stderr mentions "./pubkit.json is not JSON"

  Scenario: A repository's pubkit.json on GitHub that is not JSON is reported, and nothing sent
    Given "schryer/site"'s pubkit.json on GitHub is "{not json"
    When I run pubkit "protect" "schryer/site"
    Then it fails
    And it prints "schryer/site: not in protected.json, and its pubkit.json is not JSON"
    And GitHub was sent nothing

  Scenario: A repository's unsound protect block on GitHub is reported, and nothing sent
    Given "schryer/site"'s pubkit.json on GitHub is "{"protect": {"checks": ["build"], "tags": 0}}"
    When I run pubkit "protect" "schryer/site"
    Then it fails
    And it prints "its pubkit.json's protect block: "tags" must be true or false, not 0"
    And GitHub was sent nothing

  Scenario: protect refuses a named repository that is not OWNER/NAME
    When I run pubkit "protect" "schryer/foo/../bar"
    Then it fails
    And stderr mentions "schryer/foo/../bar is not OWNER/NAME"

  Scenario: protect refuses a protected.json entry whose tags are not true or false
    Given protected.json is {"repositories": [{"repo": "schryer/foo", "checks": ["build"], "tags": "no"}]}
    When I run pubkit "protect" "--check"
    Then it fails
    And stderr mentions "schryer/foo: "tags" must be true or false"

  Scenario: protect refuses a protected.json that lists a repository twice
    Given protected.json is {"repositories": [{"repo": "schryer/foo", "checks": ["build"]}, {"repo": "schryer/foo", "checks": ["ci / passed"]}]}
    When I run pubkit "protect" "--check"
    Then it fails
    And stderr mentions "lists schryer/foo more than once"
