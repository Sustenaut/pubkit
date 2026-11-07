Feature: pubkit deploy check walks every need, and changes nothing
  Each need is checked in order, through kubectl, the GitHub API, HTTP,
  the DNS, and the environment ./with-secrets gives: ready, missing,
  drift, blocked or unknown. A need still checked while one it waits for
  is not ready is blocked when its own check finds it missing. A check that
  overruns its budget, or is refused, is unknown, and says what access is
  missing. No value is ever printed: every fake value here holds a canary,
  and no output may.

  Background:
    Given the example service

  Scenario: Every need is ready
    When I run pubkit "deploy" "check"
    Then it succeeds
    And it prints "ready   k8s.secret:example-page-secrets  Secret example-page-secrets in example has keys cookie-secret"
    And it prints "ready   github.gate:example  DEPLOY_EXAMPLE is on, and the 15 needs before it are ready"
    And it prints "ready   deploy.rollout:example  main's abc1234 is deployed, and 2 Deployments are ready"
    And it prints "ready   upstream:storage  storage answers 200 at /healthz in"
    And it prints "deploy: 18 needs, 18 ready, 0 missing, 0 drift, 0 blocked, 0 unknown"

  Scenario: Without the namespace every k8s need waits for it, and the GitHub needs stay ready
    Given the namespace "example" is not in the cluster
    When I run pubkit "deploy" "check"
    Then the exit code is 1
    And "k8s.namespace:example" is missing
    And it prints "ssh root@server.example.eu sustenaut-app example"
    And every "k8s.secret" need is blocked, "waits for k8s.namespace:example"
    And every "k8s.manifests" need is blocked, "waits for k8s.namespace:example"
    And every "github.secret" need is ready
    And every "github.variable" need is ready
    And "github.gate:example" is drift

  Scenario: The namespace without the deploy service account is missing
    Given the namespace "example" has no "rolebinding" "deploy"
    When I run pubkit "deploy" "check"
    Then "k8s.namespace:example" is missing
    And it prints "namespace example has no rolebinding deploy"

  Scenario: A cookie secret of the wrong shape is drift, in k8s.py's words
    Given the Secret "example-page-secrets" holds "cookie-secret" as 20 bytes
    When I run pubkit "deploy" "check"
    Then the exit code is 1
    And "k8s.secret:example-page-secrets" is drift
    And it prints "Secret example-page-secrets key cookie-secret (need k8s.secret:example-page-secrets), oauth2-proxy's cookie secret: 20 bytes, 15 after URL-safe base64 decoding; oauth2-proxy needs 16, 24 or 32 bytes"

  Scenario: A Secret without a key the manifests read is drift
    Given the Secret "example-files" has no key "token"
    When I run pubkit "deploy" "check"
    Then "k8s.secret:example-files" is drift
    And it prints "Secret example-files has no key token, which need k8s.secret:example-files reads"

  Scenario: A Secret that is not there is missing, and the gate open too early is drift
    Given the Secret "example-files" is not in the cluster
    When I run pubkit "deploy" "check"
    Then "k8s.secret:example-files" is missing
    And "github.gate:example" is drift
    And it prints "DEPLOY_EXAMPLE is on while 1 need is missing"
    And it prints "gh variable set DEPLOY_EXAMPLE -R example-org/example --body off, until they are ready"

  Scenario: The gate off with everything before it ready is missing, and says how to open it
    Given the variable "DEPLOY_EXAMPLE" is "off"
    When I run pubkit "deploy" "check"
    Then the exit code is 1
    And "github.gate:example" is missing
    And it prints "DEPLOY_EXAMPLE is not on, and every need before it is ready"
    And it prints "gh variable set DEPLOY_EXAMPLE -R example-org/example --body on"

  Scenario: The gate unset while a need is not ready is blocked
    Given the variable "DEPLOY_EXAMPLE" is not set
    And the Secret "example-files" is not in the cluster
    When I run pubkit "deploy" "check"
    Then "github.gate:example" is blocked
    And it prints "DEPLOY_EXAMPLE is unset, and waits for 1 need"

  Scenario: The gate on while a need could not be checked is unknown
    Given the environment is not ./with-secrets'
    When I run pubkit "deploy" "check"
    Then "github.gate:example" is unknown
    And "bitwarden.secret:example-files-token" is unknown
    And it prints "run this through ./with-secrets, which reads the Bitwarden project"

  Scenario: An upstream that answers 503 is missing, and the service's health waits for it
    Given "https://storage.example.eu/healthz" answers 503
    And "https://example.example.eu/healthz" answers 503
    When I run pubkit "deploy" "check"
    Then the exit code is 1
    And "upstream:storage" is missing
    And it prints "storage answers 503 at /healthz"
    And "service.health:example" is blocked, "waits for upstream:storage"

  Scenario: An upstream that does not answer in time is unknown
    Given "https://storage.example.eu/healthz" does not answer in time
    When I run pubkit "deploy" "check"
    Then "upstream:storage" is unknown
    And it prints "storage did not answer at /healthz in 5 s"

  Scenario: An upstream that cannot be reached is missing
    Given "https://storage.example.eu/healthz" refuses the connection
    When I run pubkit "deploy" "check"
    Then "upstream:storage" is missing
    And it prints "storage cannot be reached at /healthz (ConnectionRefusedError)"

  Scenario: A check that overruns its budget is unknown, and left behind
    Given the "k8s.namespace" check's budget is 0.2 seconds
    And kubectl takes 1 second to show a namespace
    When I run pubkit "deploy" "check"
    Then "k8s.namespace:example" is unknown
    And it prints "the check took longer than 0.2 s, and was left"

  Scenario: A check that finishes over its budget keeps its state, and says so
    Given the "bitwarden.secret" check's budget is 0.2 seconds
    And the "bitwarden.secret" check takes 0.3 seconds, and the walk waits for it
    When I run pubkit "deploy" "check"
    Then "bitwarden.secret:example-files-token" is ready
    And it prints "over its budget of 0.2 s"

  Scenario: kubectl that may not read is unknown, and says what access is missing
    Given kubectl may not read anything
    When I run pubkit "deploy" "check"
    Then the exit code is 1
    And "k8s.namespace:example" is unknown
    And it prints "kubectl may not read namespaces in example"
    And it prints "use a kubeconfig that may get namespaces in example"
    And "k8s.secret:example-files" is unknown
    And it prints "kubectl may not read Secrets in example: the deploy identity needs get on secrets there"

  Scenario: kubectl that is not installed is unknown
    Given kubectl is not installed
    When I run pubkit "deploy" "check"
    Then "k8s.namespace:example" is unknown
    And it prints "install kubectl, or give --kubectl CMD"

  Scenario: GitHub that refuses is unknown, and says how to sign in
    Given GitHub refuses every request
    When I run pubkit "deploy" "check"
    Then "github.secret:EXAMPLE_TOKEN" is unknown
    And it prints "sign gh in as someone who administers the repository (gh auth login)"

  Scenario: Outside a GitHub repository the GitHub needs are unknown
    Given origin is not a GitHub repository
    When I run pubkit "deploy" "check"
    Then "github.secret:EXAMPLE_TOKEN" is unknown
    And "deploy.rollout:example" is unknown
    And it prints "origin is not a GitHub repository"

  Scenario: The rollout is drift when main has moved on from the last deploy
    Given main is at "def5678"
    When I run pubkit "deploy" "check"
    Then "deploy.rollout:example" is drift
    And it prints "main is at def5678, the last passed deploy is of abc1234"

  Scenario: The rollout is drift when the last deploy failed
    Given the last deploy run ended "failure"
    When I run pubkit "deploy" "check"
    Then "deploy.rollout:example" is drift
    And it prints "the last deploy, of abc1234, is failure"

  Scenario: The rollout is missing when the deploy never ran
    Given the deploy workflow never ran
    When I run pubkit "deploy" "check"
    Then "deploy.rollout:example" is missing
    And it prints "deploy.yml has not run on main"

  Scenario: The rollout is drift while a Deployment's replicas are not ready
    Given the Deployment "proxy" has 0 of 1 replicas ready
    When I run pubkit "deploy" "check"
    Then "deploy.rollout:example" is drift
    And it prints "Deployment/proxy has 0 of 1 ready"

  Scenario: A CronJob that last succeeded over a day ago is drift
    Given the CronJob "worker" last succeeded 30 hours ago
    When I run pubkit "deploy" "check"
    Then "service.health:example" is drift
    And it prints "CronJob worker last succeeded at"

  Scenario: A CronJob that never succeeded is missing
    Given the CronJob "worker" never succeeded
    When I run pubkit "deploy" "check"
    Then "service.health:example" is missing

  Scenario: A secret GitHub does not hold is missing, and says how it is sent
    Given GitHub holds no secret "EXAMPLE_TOKEN"
    When I run pubkit "deploy" "check"
    Then "github.secret:EXAMPLE_TOKEN" is missing
    And it prints "put example-token in Bitwarden, then ./with-secrets ./pubkit app-secrets"

  Scenario: A variable that differs from Bitwarden's is drift, and neither value is shown
    Given Bitwarden's "example-user" is another value
    When I run pubkit "deploy" "check"
    Then "github.variable:EXAMPLE_USER" is drift
    And it prints "variable EXAMPLE_USER in example-org/example is not Bitwarden's example-user"

  Scenario: A declared variable that is empty is missing
    Given the variable "EXAMPLE_SETTING" is ""
    When I run pubkit "deploy" "check"
    Then "github.variable:EXAMPLE_SETTING" is missing
    And it prints "variable EXAMPLE_SETTING is empty in example-org/example"

  Scenario: The server's settings must all be there, on the repository or its server environment
    Given GitHub holds no secret "TS_GH_OAUTH_SECRET"
    And GitHub holds no variable "SUSTENAUT_KNOWN_HOSTS"
    When I run pubkit "deploy" "check"
    Then "github.deploy-settings:example" is missing
    And it prints "SUSTENAUT_KNOWN_HOSTS, TS_GH_OAUTH_SECRET are set neither in example-org/example nor its server environment"

  Scenario: A Bitwarden secret that is not in the project is missing
    Given Bitwarden has no "example-files-token"
    When I run pubkit "deploy" "check"
    Then "bitwarden.secret:example-files-token" is missing
    And it prints "put example-files-token in the Bitwarden project example, and give the read-only machine account access to it"

  Scenario: A generated secret of the wrong shape is drift, said by its length alone
    Given Bitwarden's "example-cookie-secret" is 20 bytes
    When I run pubkit "deploy" "check"
    Then "bitwarden.generated:example-cookie-secret" is drift
    And it prints "example-cookie-secret is not oauth2-proxy's cookie secret: 20 bytes"

  Scenario Outline: A generated secret of each shape is ready when it has it
    Given the need "bitwarden.generated:example-cookie-secret" has the shape "<shape>"
    And Bitwarden's "example-cookie-secret" is <value>
    When I run pubkit "deploy" "check"
    Then "bitwarden.generated:example-cookie-secret" is <state>

    Examples:
      | shape     | value                  | state |
      | hex-32    | 64 hex digits          | ready |
      | hex-32    | 20 bytes               | drift |
      | base64-32 | base64 of 32 bytes     | ready |
      | base64-32 | base64 of 16 bytes     | drift |
      | base64-32 | 20 bytes               | drift |

  Scenario: The host without an address is missing
    Given the host has no address
    When I run pubkit "deploy" "check"
    Then "dns.site:example.example.eu" is missing

  Scenario: --namespace checks another namespace
    When I run pubkit "deploy" "check" "--namespace" "example-two"
    Then the exit code is 1
    And "k8s.namespace:example-two" is missing

  Scenario: No value is printed, whatever the checks find
    Given the Secret "example-page-secrets" holds "cookie-secret" as 20 bytes
    And Bitwarden's "example-cookie-secret" is 20 bytes
    And Bitwarden's "example-user" is another value
    When I run pubkit "deploy" "check" "--json"
    Then the exit code is 1
    And no output holds a value
