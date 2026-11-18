Feature: A service's needs from GitHub Actions
  A repository with a deploy block gets a service-needs workflow: run by hand with
  check or fulfil and a unit, it asks the convergence engine to check that unit's needs
  (read-only), or to fulfil them once a person approves the plan on the engine's page,
  and ends as the request ends, each need on a line of its own. The run's identity is
  its OIDC token; it holds no secret, and prints no email, link or value.

  Scenario: sync writes the caller in a repository with a deploy block, its units the choices
    Given a repository initialised as "utility"
    And the file "integrations.json" reads "{"uses": []}"
    And pubrules.json has a deploy block for "example" and the unit "other"
    When I run pubrules "sync"
    Then it succeeds
    And ".github/workflows/service-needs.yml" contains "options: ["example", "other"]"
    And ".github/workflows/service-needs.yml" contains "/pubrules/.github/workflows/service-needs.yml@"
    And ".github/workflows/service-needs.yml" contains "id-token: write"
    And ".github/workflows/service-needs.yml" contains "with: {"unit": "${{ inputs.unit }}", "kind": "${{ inputs.action }}"}"
    And ".github/workflows/service-needs.yml" contains "default: check"
    And ".github/workflows/service-needs.yml" contains "options: ["check", "fulfil"]"
    And "integrations.json" lists ".github/workflows/service-needs.yml" under "github.actions-oidc"
    When I run pubrules "sync" "--check"
    Then the caller and integrations.json are in step

  Scenario: The service-needs block and ci.runs-on reach the caller
    Given a repository initialised as "utility"
    And pubrules.json has a deploy block for "example" and the unit "other"
    And pubrules.json's service-needs block is {"engine": "https://engine.example.eu", "timeout-minutes": 60, "claude-dispatch": true}
    And pubrules.json's ci block runs on "my-runners"
    When I run pubrules "sync"
    Then it succeeds
    And ".github/workflows/service-needs.yml" contains ""engine": "https://engine.example.eu", "timeout-minutes": 60, "runs-on": "my-runners""
    And ".github/workflows/service-needs.yml" contains "service-needs.claude-dispatch is true"

  Scenario: An edited caller fails the check
    Given a repository initialised as "utility"
    And pubrules.json has a deploy block for "example" and the unit "other"
    And I run pubrules "sync"
    And the file ".github/workflows/service-needs.yml" reads "edited"
    When I run pubrules "sync" "--check"
    Then it fails
    And stderr mentions ".github/workflows/service-needs.yml differs"

  Scenario: Without a deploy block there is no caller, and a written one is to be deleted
    Given a repository initialised as "utility"
    Then the repository has no ".github/workflows/service-needs.yml"
    Given pubrules.json has a deploy block for "example" and the unit "other"
    And I run pubrules "sync"
    And pubrules.json has no deploy block
    When I run pubrules "sync" "--check"
    Then stderr mentions "delete .github/workflows/service-needs.yml: pubrules.json has no deploy block"

  Scenario Outline: A service-needs block that is wrong stops sync, saying why
    Given a repository initialised as "utility"
    And pubrules.json's service-needs block is <block>
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

  Scenario: pubrules ships the reusable service-needs workflow
    Then pubrules' service-needs.yml takes the unit, asks for id-token write, and runs pubrules service-needs

  Scenario Outline: The run ends as the request ends
    Given the engine answers the request with "planning", then "awaiting", then <end>
    When the fulfil of "example" runs for 300 minutes
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
    When the fulfil of "example" runs for 1 minutes
    Then it exits 0
    And the job summary says "still awaiting approval: the engine runs it when approved"

  Scenario: A request still running when the time is up ends well, saying the engine is still fulfilling it
    Given the engine answers the request with "running" every time
    When the fulfil of "example" runs for 1 minutes
    Then it exits 0
    And the job summary says "the engine is still fulfilling the unit"

  Scenario: A 5xx or no answer is asked again, after a longer wait each time
    Given the engine answers the request with "awaiting", then 503, then no answer, then "done" with "k8s.secret:s" ready
    When the fulfil of "example" runs for 300 minutes
    Then it exits 0
    And it waited 15, 15, 30 seconds
    And it printed "the engine answered 503; asking again in 15 s"

  Scenario: An engine that never answers runs out the time, and the run says where a request it took would be
    Given the engine never answers
    When the fulfil of "example" runs for 1 minutes
    Then it exits 1
    And the job summary says "The engine did not answer; if it took the request it is on /approvals"

  Scenario: An engine that answers 5xx while the run waits, until the time is up, ends well
    Given the engine answers the request with "awaiting", then 503 every time
    When the fulfil of "example" runs for 1 minutes
    Then it exits 0
    And the job summary says "still awaiting approval"

  Scenario: A refused run is said, and asks nothing again
    Given the engine refuses the run with 403 "not-main"
    When the fulfil of "example" runs for 300 minutes
    Then it exits 1
    And the job summary says "the engine refused the request (403, not-main)"

  Scenario: A poll the engine refuses ends the run
    Given the engine answers the request with "awaiting", then refuses with 403 "not-your-request"
    When the fulfil of "example" runs for 300 minutes
    Then it exits 1
    And the job summary says "The engine stopped answering this run"

  Scenario: An answer that is not a request is asked again
    Given the engine answers the request with "awaiting", then garbage, then "done" with "k8s.secret:s" ready
    When the fulfil of "example" runs for 300 minutes
    Then it exits 0

  Scenario: A gate ends once approved, and asks with its plan
    Given the engine answers the request with "awaiting", then "approved"
    When the gate "release" with the plan "Deploy 1.2" runs for 300 minutes
    Then it exits 0
    And the engine was asked for a gate with the plan "Deploy 1.2"

  Scenario: The token is asked of GitHub for the engine's address, afresh each call, and masked
    Given GitHub Actions gives OIDC tokens
    When the service-needs asks for two tokens for "https://engine.example.eu"
    Then GitHub was asked twice with the audience "https%3A%2F%2Fengine.example.eu"
    And each token was masked in the log

  Scenario Outline: Without an OIDC token from GitHub nothing is asked
    Given GitHub Actions <gives>
    Then asking for a token <fails>

    Examples:
      | gives                         | fails                                        |
      | gives no OIDC request address | stops, saying the job needs id-token: write |
      | answers 500 for the token     | is no answer                                 |

  Scenario: sync takes away the go-live workflow pubrules 0.43.2 wrote, and the check says so
    Given a repository initialised as "utility"
    And the go-live workflow pubrules 0.43.2 wrote is there
    When I run pubrules "sync" "--check"
    Then stderr mentions ".github/workflows/go-live.yml is the service-needs workflow now"
    When I run pubrules "sync"
    Then the repository has no ".github/workflows/go-live.yml"

  Scenario: sync renames a go-live block service-needs, and the check says it is stale
    Given a repository initialised as "utility"
    And the pubrules.json key "go-live" is {"claude-dispatch": false}
    When I run pubrules "sync" "--check"
    Then it fails
    And stderr mentions "go-live block is the service-needs block now; run `pubrules sync`, which renames it"
    When I run pubrules "sync"
    Then pubrules.json has the key "service-needs" as {"claude-dispatch": false}, and no "go-live"
    When I run pubrules "sync" "--check"
    Then stderr does not mention "go-live"

  Scenario: A pubrules.json with both a go-live and a service-needs block says to keep one
    Given a repository initialised as "utility"
    And the pubrules.json key "go-live" is {"claude-dispatch": false}
    And the pubrules.json key "service-needs" is {"claude-dispatch": true}
    When I run pubrules "sync"
    Then it fails
    And stderr mentions "both a go-live and a service-needs block: keep service-needs"

  Scenario: A check whose needs are all ready succeeds, each need on a line of its own
    Given the engine answers the request with "planning", then "done" with needs "k8s.secret:s" ready and "k8s.namespace:example" ready
    When the check of "example" runs for 300 minutes
    Then it exits 0
    And the job summary says "Every need the walk can judge is ready"
    And the log has the group "✅ k8s.secret:s ready" holding "k8s.secret:s is ready"
    And the job summary has the row "| ✅ | `k8s.namespace:example` | k8s.namespace | auto | ready | k8s.namespace:example is ready |"
    And it asked for nobody's approval

  Scenario: A check fails naming each need that is not ready, with an annotation each
    Given the engine answers the request with "planning", then "done" with needs "k8s.secrets-declared:example" drift and "k8s.manifests:k8s/admin/policy.yaml" unknown and "k8s.secret:s" ready
    When the check of "example" runs for 300 minutes
    Then it exits 1
    And the job summary says "Not ready: k8s.secrets-declared:example drift"
    And the log has the annotation "::error title=k8s.secrets-declared%3Aexample::k8s.secrets-declared:example is drift"
    And the log has the annotation "::warning title=k8s.manifests%3Ak8s/admin/policy.yaml::k8s.manifests:k8s/admin/policy.yaml is unknown"
    And the log has the group "❌ k8s.secrets-declared:example drift" holding "k8s.secrets-declared:example is drift"

  Scenario: A fulfil's groups say what the fulfil changes, and a done request's note is printed
    Given the engine answers the request with "awaiting", then "done" with needs "k8s.secret:s" ready noted "every need was made"
    When the fulfil of "example" runs for 300 minutes
    Then it exits 0
    And the log has the group "✅ k8s.secret:s ready" holding "the fulfil: the fulfil makes it: k8s.secret:s is missing"
    And it printed "example done: every need was made"

  Scenario: The ask step writes the approval box and a notice when a person must approve, and gives the request
    Given the engine answers the request with "planning", then "awaiting"
    When the ask step for "example" runs
    Then the job summary has the approval box, linking the request and naming "k8s.secret:s"
    And the log has the annotation "::notice title=Approval needed::https://convergence.example/approvals/20261117T100000Z-ab12"
    And the step output "request" is "20261117T100000Z-ab12"
    And the step output "minutes" is "299.8"
    And the ask step ends without waiting for the decision

  Scenario: The ask step of a check writes no box
    Given the engine answers the request with "planning", then "done" with needs "k8s.secret:s" ready
    When the ask step of a check of "example" runs
    Then the job summary says nothing of approval
    And the step output "request" is "20261117T100000Z-ab12"

  Scenario: The wait step follows the request the ask step gave to its end
    Given the engine answers the request with "awaiting", then "done" with needs "k8s.secret:s" ready
    When the wait step for the request "20261117T100000Z-ab12" of "example" runs for 300 minutes
    Then it exits 0
    And the engine was polled for "/requests/20261117T100000Z-ab12"

  Scenario: The wait step of a request the engine refuses, or never answers, says so
    Given the engine refuses the poll with 403 "not-your-request"
    When the wait step for the request "20261117T100000Z-ab12" of "example" runs for 300 minutes
    Then it exits 1
    Given the engine never answers
    When the wait step for the request "20261117T100000Z-ab12" of "example" runs for 1 minutes
    Then it exits 0
    And the job summary says "still awaiting approval"

  Scenario: What the engine found is printed with no email and no link
    Given the engine answers the request with "planning", then "done" with needs "k8s.secret:s" drift found "ask someone@example.org at https://example.org/x?t=1"
    When the check of "example" runs for 300 minutes
    Then the log has the group "❌ k8s.secret:s drift" holding "ask [someone] at [link]"
    And nothing it printed or summarised holds an email or the token

  Scenario: The engine refusing the run while it plans fails the ask step, saying so
    Given the engine answers the request with "planning", then refuses with 403 "not-your-request"
    When the ask step for "example" runs
    Then the ask step fails
    And the job summary says "The engine stopped answering this run"
