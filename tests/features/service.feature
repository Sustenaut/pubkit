Feature: A service runs its secret-needing commands through ./with-secrets
  A service is a private repository that deploys rather than releases.
  `pubkit init --kind=service` gives it a managed `with-secrets`, which reads
  a Bitwarden Secrets Manager project with bws and runs one command with the
  secrets in its environment, set up by the repository's with-secrets.json.

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

  Scenario: Secrets reach the command under their names, and Terraform's as TF_VAR_
    Given a service whose Terraform declares "hcloud_token"
    And Bitwarden holds "HCLOUD_TOKEN" as "h1" and "restic-password" as "r1" in the project "infra"
    When I run "./with-secrets" with the token "tok"
    Then it succeeds
    And the command sees "TF_VAR_hcloud_token" as "h1"
    And the command sees "RESTIC_PASSWORD" as "r1"
    And the command does not see "HCLOUD_TOKEN"
    And the calling shell's environment is unchanged

  Scenario: In map mode the listed names go to Terraform as two maps
    Given a service whose Terraform takes its secrets as a map of "linear-api-key"
    And Bitwarden holds "linear-api-key" as "lin" and "slack-bot-token" as "xoxb" in the project "infra"
    When I run "./with-secrets" with the token "tok"
    Then it succeeds
    And the command sees "TF_VAR_secret_values" as "{"linear-api-key":"lin"}"
    And the command sees "LINEAR_API_KEY" as "lin"
    And the command sees "SLACK_BOT_TOKEN" as "xoxb"

  Scenario: A token that cannot see the project stops before running anything
    Given a service whose Terraform declares "hcloud_token"
    And Bitwarden holds "HCLOUD_TOKEN" as "h1" and "restic-password" as "r1" in the project "other"
    When I run "./with-secrets" with the token "tok"
    Then it fails
    And stderr mentions "can't see the project infra"
    And the command did not run

  Scenario: With no token, it says where to put one
    Given a service whose Terraform declares "hcloud_token"
    When I run "./with-secrets" with no token
    Then it fails
    And stderr mentions "BWS_ACCESS_TOKEN=..."

  Scenario: The command's exit code is the wrapper's
    Given a service whose Terraform declares "hcloud_token"
    And Bitwarden holds "HCLOUD_TOKEN" as "h1" and "restic-password" as "r1" in the project "infra"
    When I run "./with-secrets" with the token "tok" and a command that exits 7
    Then the exit code is 7

  Scenario: An aliases file also exports a secret under the name a tool reads
    Given a service whose Terraform declares "hcloud_token"
    And the aliases file "infra/aliases.json" maps "TAILSCALE_OAUTH_CLIENT_ID" to "ts-tf-client-id"
    And Bitwarden holds "ts-tf-client-id" as "c1" and "restic-password" as "r1" in the project "infra"
    When I run "./with-secrets" with the token "tok"
    Then it succeeds
    And the command sees "TAILSCALE_OAUTH_CLIENT_ID" as "c1"
    And the command sees "TS_TF_CLIENT_ID" as "c1"
    And the command sees "RESTIC_PASSWORD" as "r1"

  Scenario: An alias of a secret that is not in Bitwarden is reported and skipped
    Given a service whose Terraform declares "hcloud_token"
    And the aliases file "infra/aliases.json" maps "TAILSCALE_OAUTH_CLIENT_ID" to "missing"
    And Bitwarden holds "HCLOUD_TOKEN" as "h1" and "restic-password" as "r1" in the project "infra"
    When I run "./with-secrets" with the token "tok"
    Then it succeeds
    And stderr mentions "no secret 'missing' in Bitwarden"
    And the command does not see "TAILSCALE_OAUTH_CLIENT_ID"

  Scenario: An alias must be usable as an environment variable name
    Given a service whose Terraform declares "hcloud_token"
    And the aliases file "infra/aliases.json" maps "tailscale-id" to "ts-tf-client-id"
    And Bitwarden holds "ts-tf-client-id" as "c1" and "restic-password" as "r1" in the project "infra"
    When I run "./with-secrets" with the token "tok"
    Then it fails
    And stderr mentions "not usable as an environment variable name"
    And the command did not run
