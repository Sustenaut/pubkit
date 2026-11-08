Feature: pubkit deploy walk --fulfil makes what is missing, once the plan is confirmed
  The walk checks every need first. With --fulfil it prints the plan, a line
  per need it will act on, and waits for a typed yes (--yes skips that); a
  write token is asked for at a hidden prompt when a need wants one. Each
  auto need that is missing, and whose dependencies are ready, is fulfilled,
  then checked again, and that check is its result. A fulfil that fails
  leaves the need as it was, with the failure as a detail. A propose need's
  fix is its proposal; nothing acts on it. Every fake value holds a canary,
  and no output, journal or argv may.

  Background:
    Given the example service

  Scenario: Without a typed yes the plan is printed and nothing is done
    Given the Secret "example-files" is not in the cluster
    And "no" is typed at the prompt
    When I run pubkit "deploy" "walk" "--fulfil"
    Then the exit code is 1
    And stderr mentions "deploy: the walk will:"
    And stderr mentions "k8s.secret:example-files: apply Secret example-files in example from bitwarden.secret:example-files-token, and restart what reads it"
    And stderr mentions "deploy: nothing was fulfilled"
    And "k8s.secret:example-files" is missing
    And kubectl was given nothing to write

  Scenario: A typed yes makes the Secret from Bitwarden's value, on kubectl's standard input, and restarts what reads it
    Given the Secret "example-files" is not in the cluster
    And "yes" is typed at the prompt
    When I run pubkit "deploy" "walk" "--fulfil"
    Then it succeeds
    And "k8s.secret:example-files" is ready, "(fulfilled)"
    And it prints "fulfilled: made Secret example-files in example from Bitwarden example-files-token, 17 bytes"
    And it prints "restarted Deployment/app, which reads Secret example-files"
    And the cluster's Secret "example-files" holds Bitwarden's "example-files-token" as "token"
    And "github.gate:example" is ready
    And no output holds a value

  Scenario: A declined plan asks for no token, and leaves each need as its check found it
    Given Bitwarden has no "example-cookie-secret"
    And "no" is typed at the prompt
    When I run pubkit "deploy" "walk" "--fulfil"
    Then the write token was never asked for
    And "bitwarden.generated:example-cookie-secret" is missing
    And it does not print "needs a Bitwarden write token"

  Scenario: --tolerate-unknown fails only for a need missing, drifted or blocked
    Given the environment is not ./with-secrets'
    When I run pubkit "deploy" "walk" "--tolerate-unknown"
    Then it succeeds
    And "bitwarden.secret:example-files-token" is unknown

  Scenario: --tolerate-unknown still fails for a missing need
    Given the environment is not ./with-secrets'
    And the Secret "example-files" is not in the cluster
    When I run pubkit "deploy" "walk" "--tolerate-unknown"
    Then the exit code is 1

  Scenario: Without --fulfil nothing is planned or done
    Given the Secret "example-files" is not in the cluster
    When I run pubkit "deploy" "walk"
    Then the exit code is 1
    And stderr does not mention "the walk will"
    And kubectl was given nothing to write

  Scenario: A generated secret is made, written with the token asked for once, and fills its Secret in the same walk
    Given Bitwarden has no "example-cookie-secret"
    And the Secret "example-page-secrets" is not in the cluster
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then it succeeds
    And the write token was asked for once
    And "bitwarden.generated:example-cookie-secret" is ready, "(fulfilled)"
    And Bitwarden was given "example-cookie-secret", 43 bytes
    And "k8s.secret:example-page-secrets" is ready, "(fulfilled)"
    And the cluster's Secret "example-page-secrets" holds what Bitwarden was given for "example-cookie-secret" as "cookie-secret"
    And it prints "restarted Deployment/proxy, which reads Secret example-page-secrets"

  Scenario: A Secret ready by its keys is made again when the walk changes a value it is made from
    Given Bitwarden's "example-cookie-secret" is 20 bytes
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then it succeeds
    And stderr mentions "k8s.secret:example-page-secrets: apply Secret example-page-secrets in example from bitwarden.generated:example-cookie-secret, and restart what reads it, again: bitwarden.generated:example-cookie-secret changes"
    And "bitwarden.generated:example-cookie-secret" is ready, "(fulfilled)"
    And "k8s.secret:example-page-secrets" is ready, "(fulfilled)"
    And the cluster's Secret "example-page-secrets" holds what Bitwarden was given for "example-cookie-secret" as "cookie-secret"
    And it prints "restarted Deployment/proxy, which reads Secret example-page-secrets"

  Scenario: A Secret whose values the walk did not change is left alone
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then it succeeds
    And kubectl was given nothing to write

  Scenario Outline: Without a terminal no token is asked for, and the needs that want one are unknown
    Given Bitwarden has no "example-cookie-secret"
    And the Secret "example-page-secrets" is not in the cluster
    And <where>
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then the exit code is 1
    And the write token was never asked for
    And "bitwarden.generated:example-cookie-secret" is unknown, "not fulfilled: needs a Bitwarden write token"
    And it prints "needs a Bitwarden write token: run the walk at a terminal"
    And "k8s.secret:example-page-secrets" is blocked

    Examples:
      | where                           |
      | the walk has no terminal        |
      | the walk runs in GitHub Actions |

  Scenario: A fulfil that fails keeps its state, says why, and the walk goes on
    Given the Secret "example-files" is not in the cluster
    And kubectl may write nothing
    And GitHub holds no secret "EXAMPLE_TOKEN"
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then the exit code is 1
    And "k8s.secret:example-files" is missing
    And it prints "fulfil failed: kubectl apply of Secret example-files in example failed"
    And "github.secret:EXAMPLE_TOKEN" is ready, "(fulfilled)"
    And no output holds a value

  Scenario: Over the read-only deploy channel every write is unknown
    Given the walk reads the cluster over the deploy channel
    And the Secret "example-files" is not in the cluster
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then "k8s.secret:example-files" is unknown, "not fulfilled: needs the admin channel or the plane"
    And it prints "found blocked: waits for k8s.namespace:example: Secret example-files is not in example"
    And "k8s.namespace:example" is unknown, "judged over the admin channel only"
    And kubectl was given nothing to write

  Scenario: The namespace is made over the admin channel, and what was blocked on it is made next
    Given the namespace "example" is not in the cluster
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then "k8s.namespace:example" is ready, "(fulfilled)"
    And the server ran "sustenaut-app example"
    And "k8s.secret:example-files" is ready, "(fulfilled)"
    And "k8s.manifests:k8s/admin/network-policy.yaml" is ready, "(fulfilled)"
    And "k8s.secret:example-app-env" is missing

  Scenario: Without the admin channel the namespace is unknown, saying what it needs
    Given the namespace "example" is not in the cluster
    And no command runs on the server
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then "k8s.namespace:example" is unknown, "not fulfilled: needs the admin channel (--server HOST) or the plane"

  Scenario: The gate off with every need before it ready is set on, and the workflow run
    Given the variable "DEPLOY_EXAMPLE" is "off"
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then it succeeds
    And "github.gate:example" is ready, "(fulfilled)"
    And it prints "fulfilled: set DEPLOY_EXAMPLE on, and ran deploy.yml on main"
    And deploy.yml was run on "main" once

  Scenario: The gate is not opened while a need before it is not ready
    Given the variable "DEPLOY_EXAMPLE" is "off"
    And the Secret "example-app-env" is not in the cluster
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then "github.gate:example" is blocked
    And the variable "DEPLOY_EXAMPLE" is still "off"

  Scenario: A failed deploy is run again, and waited for
    Given the last deploy run ended "failure"
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then it succeeds
    And "deploy.rollout:example" is ready, "(fulfilled)"
    And it prints "fulfilled: ran deploy.yml on main: run 8 passed"

  Scenario: A deploy run again that fails is said, and the rollout stays drift
    Given the last deploy run ended "failure"
    And a deploy run by hand ends "failure"
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then "deploy.rollout:example" is drift
    And it prints "fulfil failed: deploy.yml run 8 ended failure"

  Scenario: A GitHub secret is set from Bitwarden through gh, the value on its standard input
    Given GitHub holds no secret "EXAMPLE_TOKEN"
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then it succeeds
    And it prints "fulfilled: set secret EXAMPLE_TOKEN in example-org/example from Bitwarden's example-token, 17 bytes"
    And gh set "secret" "EXAMPLE_TOKEN"

  Scenario: The deploy settings are set from the secrets block's entries
    Given GitHub holds no secret "SUSTENAUT_DEPLOY_SSH_KEY"
    And Bitwarden's "example-deploy-key" is another value
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then "github.deploy-settings:example" is ready, "(fulfilled)"
    And gh set "secret" "SUSTENAUT_DEPLOY_SSH_KEY"

  Scenario: A variable the state file keeps is proposed, not set
    Given GitHub holds no variable "EXAMPLE_SETTING"
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then "github.variable:EXAMPLE_SETTING" is missing
    And it prints "then pubkit variables --apply example"
    And stderr does not mention "github.variable:EXAMPLE_SETTING"

  Scenario: A CronJob that never succeeded is run, and waited for
    Given the CronJob "worker" never succeeded
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then it succeeds
    And "service.health:example" is ready, "(fulfilled)"
    And a Job was made from the CronJob "worker"

  Scenario: service.health counts a Job made from its CronJob by hand
    Given the CronJob "worker" never succeeded
    And the Job "worker-now", made from it by hand, completed 2 hours ago
    When I run pubkit "deploy" "walk"
    Then "service.health:example" is ready

  Scenario: A host that does not answer is not acted on
    Given "https://example.example.eu/healthz" answers 502
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then "service.health:example" is missing
    And stderr does not mention "service.health"
