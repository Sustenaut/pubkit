Feature: A service runs its secret-needing commands through ./with-secrets
  A service is a private repository that deploys rather than releases.
  `pubkit init --kind=service` gives it a managed `with-secrets`, which reads
  a Bitwarden Secrets Manager project with bws and runs one command with the
  secrets in its environment, set up by the repository's with-secrets.json.
  That script is one line that runs `./pubsec run`, and ./pubsec, which
  pubkit writes beside it, runs the pubsec release pubkit pins, its wheel
  checked against its sha256: what `pubsec run` does is pubsec's suite's.

  Scenario: init writes the wrapper, its settings and the CI its ci block gives
    Given an empty repository
    When I run pubkit "init" "--kind=service" "--project=infra"
    Then it succeeds
    And the repository has "with-secrets", "with-secrets.json", ".github/workflows/pr.yml", ".github/workflows/main.yml", "tests/requirements-pubkit.txt"
    And "with-secrets" is executable
    And "with-secrets.json" contains ""project": "infra""
    And ".github/workflows/pr.yml" contains "job-service-checks.yml@v"
  Scenario: A service names the project its secrets are in
    Given an empty repository
    When I run pubkit "init" "--kind=service"
    Then it fails
    And stderr mentions "--project=NAME"
  Scenario: with-secrets is managed, so an edited copy fails the check
    Given a repository initialised as service for the project "infra"
    And the file "with-secrets" reads "edited"
    When I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "with-secrets differs"
    When I run pubkit "sync"
    Then it succeeds
    And "with-secrets" is executable
  Scenario Outline: A kind pubkit no longer has names the one to use
    Given an empty repository
    And the file "pubkit.json" reads "{"kind": "<old>"}"
    When I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "the kind "<old>" is now "utility""

    Examples:
      | old            |
      | python         |
      | python-utility |
  Scenario: sync moves a Bitwarden use declared on with-secrets to with-secrets.json
    Given a repository initialised as service for the project "infra"
    And the file "integrations.json" reads "{"uses": [{"plugin": "bitwarden.secrets", "paths": ["with-secrets", "push-secrets.sh"], "mode": "required"}]}"
    When I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "or with-secrets.json for Bitwarden"
    When I run pubkit "sync"
    Then "integrations.json" contains ""push-secrets.sh""
    And "integrations.json" contains ""with-secrets.json""
    And "integrations.json" does not contain ""with-secrets","
    When I run pubkit "sync" "--check"
    Then it succeeds
  Scenario: A repository that does not run pubkit's with-secrets keeps its declaration
    Given a repository initialised as "utility"
    And the file "integrations.json" reads "{"uses": [{"plugin": "bitwarden.secrets", "paths": ["with-secrets"], "mode": "required"}]}"
    When I run pubkit "sync"
    Then "integrations.json" does not contain "with-secrets.json"

  Scenario: deploy.providers' wheels join the tests' requirements, where sync --check reads their need types
    Given a repository initialised as "utility"
    And pubkit.json has a deploy block
    When I run pubkit "sync"
    Then it succeeds
    And "tests/requirements-pubkit.txt" lists the pubsec of deploy.providers by its wheel

  Scenario: Without a deploy block the tests' requirements name no provider
    Given a repository initialised as "utility"
    Then "tests/requirements-pubkit.txt" does not contain "pubsec"

  Scenario: init writes ./pubsec beside ./with-secrets, which runs it
    Given an empty repository
    When I run pubkit "init" "--kind=service" "--project=infra"
    Then it succeeds
    And "pubsec" is executable
    And "with-secrets" contains "/pubsec" run --cwd "$PWD" -- "$@""
    And "pubsec" runs the oldest pubsec this pubkit works with

  Scenario: ./with-secrets runs ./pubsec run in the directory it is called from, from the checked wheel
    Given a repository initialised as service for the project "infra"
    And deploy.providers lists a pubsec wheel in the cache, at "9.9.9"
    And uvx records how it was run
    When I run ./with-secrets "terraform" "plan" in the directory "infra"
    Then it succeeds
    And uvx ran pubsec from the cached wheel with "run --cwd {repo}/infra -- terraform plan"

  Scenario: ./pubsec refuses a wheel that is not the one pinned
    Given a repository initialised as service for the project "infra"
    And deploy.providers lists a pubsec wheel in the cache, at "9.9.9"
    And the cached wheel has changed
    And uvx records how it was run
    When I run ./with-secrets "terraform" "plan" in the directory "infra"
    Then it fails
    And stderr mentions "is not the wheel deploy.providers pins"
    And uvx was not run

  Scenario: Any repository with a with-secrets.json gets ./pubsec
    Given a repository initialised as "utility"
    And the file "with-secrets.json" reads "{"project": "infra"}"
    When I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "pubsec differs"
    When I run pubkit "sync"
    Then "pubsec" is executable
    And "with-secrets" is executable
