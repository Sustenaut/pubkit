Feature: The need types phase 2 adds: a service account key, a sign-in client, a storage tenant, a variable's entry and a CronJob
  Each is checked as every need is, and fulfilled when its grade is auto:
  gcp.service-account-key makes a key and writes it to Bitwarden, deleting
  it from Google when that fails; auth.sign-in-client drives register-app.py
  on the server; storage.tenant and github.variable-entry propose the entry
  to add; k8s.cronjob runs a Job from the CronJob. No value is shown: a key
  is named by six characters of its id, and every fake value holds a canary.

  Background:
    Given the example service

  # --- gcp.service-account-key -------------------------------------------------

  Scenario: A key in Bitwarden that is one of the account's live keys is ready
    Given the deploy block also needs {"type": "gcp.service-account-key", "account": "example-files", "project": "example-project", "bitwarden": "example-files-key"}
    And Bitwarden holds "example-files-key" as the live key "0123456789abcdef0123456789abcdef01234567" of "example-files@example-project.iam.gserviceaccount.com"
    When I run pubkit "deploy" "walk"
    Then "gcp.service-account-key:example-files-key" is ready, "key 012345 is live"
    And no output holds a value

  Scenario: A key that is not one of the account's live keys is drift
    Given the deploy block also needs {"type": "gcp.service-account-key", "account": "example-files@example-project.iam.gserviceaccount.com", "bitwarden": "example-files-key"}
    And Bitwarden holds "example-files-key" as a key "fedcba9876543210fedcba9876543210fedcba98" the account does not have
    When I run pubkit "deploy" "walk"
    Then "gcp.service-account-key:example-files-key" is drift, "the key in Bitwarden is not one of the account's live keys: make a new one"

  Scenario: An account that is not there is Terraform's, and is not acted on
    Given the deploy block also needs {"type": "gcp.service-account-key", "account": "example-gone", "project": "example-project", "bitwarden": "example-files-key"}
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then "gcp.service-account-key:example-files-key" is missing
    And it prints "the account is Terraform's: add it in the stack and apply"
    And Bitwarden was given nothing

  Scenario: Without gcloud the key is unknown
    Given the deploy block also needs {"type": "gcp.service-account-key", "account": "example-files", "project": "example-project", "bitwarden": "example-files-key"}
    And gcloud is not installed
    When I run pubkit "deploy" "walk"
    Then "gcp.service-account-key:example-files-key" is unknown, "gcloud did not run"

  Scenario: A key not in Bitwarden is made, written there, and is live
    Given the deploy block also needs {"type": "gcp.service-account-key", "account": "example-files", "project": "example-project", "bitwarden": "example-files-key"}
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then "gcp.service-account-key:example-files-key" is ready, "(fulfilled)"
    And it prints "made key 111111 of example-files@example-project.iam.gserviceaccount.com, and wrote it to Bitwarden as example-files-key"
    And Bitwarden was given "example-files-key" as a key of "example-files@example-project.iam.gserviceaccount.com"
    And no output holds a value

  Scenario: A key Bitwarden refuses is deleted from Google
    Given the deploy block also needs {"type": "gcp.service-account-key", "account": "example-files", "project": "example-project", "bitwarden": "example-files-key"}
    And Bitwarden refuses every write
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then "gcp.service-account-key:example-files-key" is missing
    And it prints "key 111111 was deleted from Google, as nothing holds it"
    And "example-files@example-project.iam.gserviceaccount.com" has no key
    And no output holds a value

  # --- auth.sign-in-client --------------------------------------------------------

  Scenario: A registered client whose Secret has both keys is ready
    Given the service signs in with a client of example.eu
    And the client is "registered"
    And the Secret "example-oidc" holds the client
    When I run pubkit "deploy" "walk"
    Then "auth.sign-in-client:example" is ready, "the example.eu client is registered, and Secret example-oidc has its keys"
    And register-app.py came on the server's standard input, with "--status"

  Scenario: A registered client without its Secret has it made on the server, the value never leaving it
    Given the service signs in with a client of example.eu
    And the client is "registered"
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then "auth.sign-in-client:example" is ready, "(fulfilled)"
    And it prints "fulfilled: made Secret example-oidc in example on the server, from the client kept there"
    And "k8s.secret:example-oidc" is ready
    And no output holds a value

  Scenario: A client waiting for its approval record has the record written into the approvals file
    Given the service signs in with a client of example.eu
    And the client is "waiting", with the record "_example-auth.example.eu" "approve-example"
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then "auth.sign-in-client:example" is blocked, "(fulfilled)"
    And it prints "fulfilled: wrote the approval record into ../infra/approvals.json: commit it, merge, apply"
    And the approvals file holds "approve-example"

  Scenario: A client no one signed in for is a person's step
    Given the service signs in with a client of example.eu
    And the client is "none"
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then "auth.sign-in-client:example" is blocked, "a person signs in"
    And it prints "it prints a link and a code; sign in there as the registrant, with the role example on"
    And stderr does not mention "auth.sign-in-client"

  Scenario: Without the admin channel the client is unknown
    Given the service signs in with a client of example.eu
    And no command runs on the server
    When I run pubkit "deploy" "walk"
    Then "auth.sign-in-client:example" is unknown, "judged over the admin channel only"

  # --- storage.tenant -------------------------------------------------------------

  Scenario: A tenant declared with its principals, and deployed, is ready
    Given the service is a private tenant of the storage
    When I run pubkit "deploy" "walk"
    Then "storage.tenant:example" is ready, "tenant example is in example-org/storage's tenants.json, and storage answers 401 for it"

  Scenario: A tenant not declared is missing, and its entry proposed
    Given the service is a private tenant of the storage
    And the storage's tenants.json has no "example"
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then "storage.tenant:example" is missing
    And it prints "in example-org/storage's tenants.json, add or extend:"
    And it prints "{"private": true, "read": ["k8s:example/app"]}"

  Scenario: A principal the tenant does not list is drift
    Given the service is a private tenant of the storage
    And the storage's tenant "example" lets nothing read
    When I run pubkit "deploy" "walk"
    Then "storage.tenant:example" is drift, "tenant example in example-org/storage does not let k8s:example/app read"

  Scenario: A tenant declared but not deployed is drift
    Given the service is a private tenant of the storage
    And "https://files.example.eu/example/list?prefix=f/" answers 404
    When I run pubkit "deploy" "walk"
    Then "storage.tenant:example" is drift, "tenant example is declared but not deployed yet"

  # --- github.variable-entry ------------------------------------------------------

  Scenario: A JSON variable with the entry is ready, its value compared and not shown
    Given another repository's variable holds the entry
    When I run pubkit "deploy" "walk"
    Then "github.variable-entry:example-org/releases/RELEASE_STATE/evaluator" is ready, "has /evaluator, the need's value"
    And no output holds a value

  Scenario: A JSON variable without the entry is missing, and the state file's change proposed
    Given another repository's variable holds the entry
    And the variable's entry is not there
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then "github.variable-entry:example-org/releases/RELEASE_STATE/evaluator" is missing
    And it prints "set /evaluator of variables.RELEASE_STATE to {"version": "1.2.0"}"

  Scenario: An entry of another value is drift
    Given another repository's variable holds the entry
    And the variable's entry is another value
    When I run pubkit "deploy" "walk"
    Then "github.variable-entry:example-org/releases/RELEASE_STATE/evaluator" is drift, "/evaluator of variable RELEASE_STATE in example-org/releases is not the need's value"
    And no output holds a value

  # --- k8s.cronjob ----------------------------------------------------------------

  Scenario: A CronJob that succeeded within twice its schedule's interval is ready
    Given the deploy block also needs {"type": "k8s.cronjob", "name": "worker"}
    When I run pubkit "deploy" "walk"
    Then "k8s.cronjob:worker" is ready, "within 2 h"

  Scenario: A CronJob whose last success is older than that is drift
    Given the deploy block also needs {"type": "k8s.cronjob", "name": "worker"}
    And the CronJob "worker" last succeeded 3 hours ago
    When I run pubkit "deploy" "walk"
    Then "k8s.cronjob:worker" is drift, "over 2 h ago"

  Scenario: A CronJob that never ran is run, and waited for
    Given the deploy block also needs {"type": "k8s.cronjob", "name": "worker"}
    And the CronJob "worker" never succeeded
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then "k8s.cronjob:worker" is ready, "(fulfilled)"
    And a Job was made from the CronJob "worker"

  Scenario: A suspended CronJob is drift, and not acted on
    Given the deploy block also needs {"type": "k8s.cronjob", "name": "worker"}
    And the CronJob "worker" is suspended
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then "k8s.cronjob:worker" is drift, "is suspended"
    And kubectl was given nothing to write

  Scenario Outline: A schedule pubkit cannot read needs within
    Given the deploy block also needs <need>
    And the CronJob "worker" runs on "<schedule>"
    When I run pubkit "deploy" "walk"
    Then "k8s.cronjob:worker" is <state>, "<text>"

    Examples:
      | need                                                       | schedule        | state   | text                                 |
      | {"type": "k8s.cronjob", "name": "worker"}                  | 0 9 1-7 * 1     | unknown | is not one pubkit reads              |
      | {"type": "k8s.cronjob", "name": "worker", "within": "36h"} | 0 9 1-7 * 1     | ready   | within 36 h                          |
      | {"type": "k8s.cronjob", "name": "worker"}                  | */15 * * * *    | drift   | over 0.5 h ago                       |
      | {"type": "k8s.cronjob", "name": "worker"}                  | 30 2 * * *      | ready   | within 48 h                          |
      | {"type": "k8s.cronjob", "name": "worker"}                  | @weekly         | ready   | within 336 h                         |

  Scenario: Over the deploy channel the CronJob is read, and running it is unknown
    Given the deploy block also needs {"type": "k8s.cronjob", "name": "worker"}
    And the CronJob "worker" never succeeded
    And the walk reads the cluster over the deploy channel
    When I run pubkit "deploy" "walk" "--fulfil" "--yes"
    Then "k8s.cronjob:worker" is unknown, "not fulfilled: needs the admin channel or the plane"
    And it prints "CronJob worker never ran"
