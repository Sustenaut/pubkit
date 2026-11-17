Feature: Go-live from GitHub Actions
  A repository with a deploy block gets a go-live workflow: run by hand with a
  unit, it asks the convergence engine to fulfil that unit, waits while a person
  approves the plan on the engine's page, and ends as the request ends. The run's
  identity is its OIDC token; it holds no secret, and prints no email or value.

  Scenario: sync writes the caller in a repository with a deploy block, its units the choices
    Given a repository initialised as "utility"
    And the file "integrations.json" reads "{"uses": []}"
    And pubrules.json has a deploy block for "example" and the unit "other"
    When I run pubrules "sync"
    Then it succeeds
    And ".github/workflows/go-live.yml" contains "options: ["example", "other"]"
    And ".github/workflows/go-live.yml" contains "/pubrules/.github/workflows/go-live.yml@"
    And ".github/workflows/go-live.yml" contains "id-token: write"
    And ".github/workflows/go-live.yml" contains "with: {"unit": "${{ inputs.unit }}"}"
    And "integrations.json" lists ".github/workflows/go-live.yml" under "github.actions-oidc"
    When I run pubrules "sync" "--check"
    Then the caller and integrations.json are in step

  Scenario: The go-live block and ci.runs-on reach the caller
    Given a repository initialised as "utility"
    And pubrules.json has a deploy block for "example" and the unit "other"
    And pubrules.json's go-live block is {"engine": "https://engine.example.eu", "timeout-minutes": 60, "claude-dispatch": true}
    And pubrules.json's ci block runs on "my-runners"
    When I run pubrules "sync"
    Then it succeeds
    And ".github/workflows/go-live.yml" contains ""engine": "https://engine.example.eu", "timeout-minutes": 60, "runs-on": "my-runners""
    And ".github/workflows/go-live.yml" contains "go-live.claude-dispatch is true"

  Scenario: An edited caller fails the check
    Given a repository initialised as "utility"
    And pubrules.json has a deploy block for "example" and the unit "other"
    And I run pubrules "sync"
    And the file ".github/workflows/go-live.yml" reads "edited"
    When I run pubrules "sync" "--check"
    Then it fails
    And stderr mentions ".github/workflows/go-live.yml differs"

  Scenario: Without a deploy block there is no caller, and a written one is to be deleted
    Given a repository initialised as "utility"
    Then the repository has no ".github/workflows/go-live.yml"
    Given pubrules.json has a deploy block for "example" and the unit "other"
    And I run pubrules "sync"
    And pubrules.json has no deploy block
    When I run pubrules "sync" "--check"
    Then stderr mentions "delete .github/workflows/go-live.yml: pubrules.json has no deploy block"

  Scenario Outline: A go-live block that is wrong stops sync, saying why
    Given a repository initialised as "utility"
    And pubrules.json's go-live block is <block>
    When I run pubrules "sync" "--check"
    Then it fails
    And stderr mentions "<said>"

    Examples:
      | block                                  | said                                                   |
      | []                                     | it must be an object                                   |
      | {"other": 1}                           | other is not a setting; it takes engine, timeout-minutes, claude-dispatch |
      | {"engine": "http://engine.example.eu"} | engine must be the convergence engine's https:// address |
      | {"timeout-minutes": 0}                 | timeout-minutes must be a whole number of minutes      |
      | {"claude-dispatch": "yes"}             | claude-dispatch must be true or false                  |

  Scenario: pubrules ships the reusable go-live workflow
    Then pubrules's go-live.yml takes the unit, asks for id-token write, and runs pubrules go-live

  Scenario Outline: The run ends as the request ends
    Given the engine answers the request with "planning", then "awaiting", then <end>
    When the go-live of "example" runs for 300 minutes
    Then it exits <code>
    And it printed each state once: <printed>
    And the job summary links the approval page
    And the job summary says "<summary>"
    And nothing it printed or summarised holds an email or the token

    Examples:
      | end                                             | code | printed                                              | summary                                     |
      | "done" with "k8s.secret:s" ready                 | 0    | planning, awaiting, done                              | Done.                                       |
      | "declined"                                      | 1    | planning, awaiting, declined                          | Declined                                    |
      | "expired"                                       | 1    | planning, awaiting, expired                           | Expired: nobody decided in time             |
      | "failed" saying "plan changed since approval"   | 1    | planning, awaiting, failed                            | Failed: plan changed since approval         |
      | "failed" saying "approved by someone@example.org" | 1  | planning, awaiting, failed                            | Failed: approved by [someone]               |

  Scenario: A request still awaiting when the time is up ends well, saying the engine runs it when approved
    Given the engine answers the request with "awaiting" every time
    When the go-live of "example" runs for 1 minutes
    Then it exits 0
    And the job summary says "still awaiting approval: the engine runs it when approved"

  Scenario: A request still running when the time is up ends well, saying the engine finishes it
    Given the engine answers the request with "running" every time
    When the go-live of "example" runs for 1 minutes
    Then it exits 0
    And the job summary says "still the engine is fulfilling the unit: the engine finishes it"

  Scenario: A 5xx or no answer is asked again, after a longer wait each time
    Given the engine answers the request with "awaiting", then 503, then no answer, then "done" with "k8s.secret:s" ready
    When the go-live of "example" runs for 300 minutes
    Then it exits 0
    And it waited 15, 15, 30 seconds
    And it printed "the engine answered 503; asking again in 15 s"

  Scenario: An engine that never answers runs out the time and asks nothing more
    Given the engine never answers
    When the go-live of "example" runs for 1 minutes
    Then it exits 1
    And the job summary says "The engine did not answer: nothing was asked"

  Scenario: An engine that answers 5xx while the run waits, until the time is up, ends well
    Given the engine answers the request with "awaiting", then 503 every time
    When the go-live of "example" runs for 1 minutes
    Then it exits 0
    And the job summary says "still awaiting approval"

  Scenario: A refused run is said, and asks nothing again
    Given the engine refuses the run with 403 "not-main"
    When the go-live of "example" runs for 300 minutes
    Then it exits 1
    And the job summary says "the engine refused the request (403, not-main)"

  Scenario: A poll the engine refuses ends the run
    Given the engine answers the request with "awaiting", then refuses with 403 "not-your-request"
    When the go-live of "example" runs for 300 minutes
    Then it exits 1
    And the job summary says "The engine stopped answering this run"

  Scenario: An answer that is not a request is asked again
    Given the engine answers the request with "awaiting", then garbage, then "done" with "k8s.secret:s" ready
    When the go-live of "example" runs for 300 minutes
    Then it exits 0

  Scenario: A gate ends once approved, and asks with its plan
    Given the engine answers the request with "awaiting", then "approved"
    When the gate "release" with the plan "Deploy 1.2" runs for 300 minutes
    Then it exits 0
    And the engine was asked for a gate with the plan "Deploy 1.2"

  Scenario: The token is asked of GitHub for the engine's address, afresh each call, and masked
    Given GitHub Actions gives OIDC tokens
    When the go-live asks for two tokens for "https://engine.example.eu"
    Then GitHub was asked twice with the audience "https%3A%2F%2Fengine.example.eu"
    And each token was masked in the log

  Scenario Outline: Without an OIDC token from GitHub nothing is asked
    Given GitHub Actions <gives>
    Then asking for a token <fails>

    Examples:
      | gives                         | fails                                        |
      | gives no OIDC request address | stops, saying the job needs id-token: write |
      | answers 500 for the token     | is no answer                                 |
