Feature: pubkit's retry and terraform-init actions try a flaky step again
  A release or provider download can time out, and pip retries no 504, so
  the reusable workflows try their setup and terraform init again. The two
  composite actions hold that loop once: retry runs a bash script up to a
  number of times, warning and waiting after each failed try but the last;
  terraform-init runs init through the same loop, then once from
  pubkit.json's provider mirror. Each scenario runs the action's step as
  GitHub does, with its inputs in the environment, a sleep that only
  records how long it was asked to wait, and a stub terraform.

  Scenario: A script that fails once is tried again, and passes
    When the retry action runs a script that fails the first time
    Then the script ran 2 times, and the step passed
    And it warned "setup failed (attempt 1 of 3)"
    And it slept 1 time for 15 seconds

  Scenario: A script that always fails is tried three times, waiting only between tries, and the step fails
    When the retry action runs a script that always fails
    Then the script ran 3 times, and the step failed with 1
    And it warned "setup failed (attempt 1 of 3)", "setup failed (attempt 2 of 3)" and "setup failed (attempt 3 of 3)"
    And it slept 2 times for 15 seconds

  Scenario: tries, pause and what are the caller's
    When the retry action runs a script that always fails, with what "npm ci", tries "5" and pause "7"
    Then the script ran 5 times, and the step failed with 1
    And it warned "npm ci failed (attempt 5 of 5)"
    And it slept 4 times for 7 seconds

  Scenario: One try waits for nothing
    When the retry action runs a script that always fails, with what "setup", tries "1" and pause "7"
    Then the script ran 1 times, and the step failed with 1
    And it slept 0 times for 7 seconds

  Scenario: Each try runs the script under bash -e, so a failing line fails the try
    When the retry action runs the script "echo run >> $RUNS; false; echo reached > $RUNS.after"
    Then the script ran 3 times, and the step failed with 1
    And no try went past the failing line

  Scenario: The script is run as given, never expanded by GitHub or the shell first
    When the retry action runs the script "echo run >> $RUNS; printf %s '${{ github.token }} $(touch hacked)' > $RUNS.text"
    Then the script ran 1 times, and the step passed
    And the script printed "${{ github.token }} $(touch hacked)", and no file "hacked" was made
    And every step of the "retry" and "terraform-init" actions takes its inputs from its environment alone

  Scenario Outline: A tries or pause that is not a number stops the step before any try
    When the retry action runs a script that always fails, with what "setup", tries "<tries>" and pause "<pause>"
    Then the script ran 0 times, and the step failed with 2
    And it reported the error "<error>"

    Examples:
      | tries | pause | error                                         |
      | 0     | 15    | tries must be a whole number above 0, not '0' |
      | three | 15    | tries must be a whole number above 0          |
      | 3     | -1    | pause must be a whole number of seconds       |

  Scenario: An init that passes is run once, with the directory and arguments given
    When the terraform-init action runs in "infra" with "-backend=false -input=false", with a terraform that passes, and the mirror "https://storage.example/terraform"
    Then terraform init ran 1 times, and the step passed
    And every init ran "-chdir=infra init -backend=false -input=false"
    And no init was given a CLI configuration

  Scenario: An init that fails is tried three times, then once from pubkit.json's provider mirror
    When the terraform-init action runs in "infra" with "-backend=false -input=false", with a terraform that fails unless given a provider mirror, and the mirror "https://storage.example/terraform"
    Then terraform init ran 4 times, and the step passed
    And the last init's CLI configuration installs every provider from "https://storage.example/terraform/"
    And it warned "terraform init failed (attempt 3 of 3)" and "terraform init: trying the providers from https://storage.example/terraform"
    And it slept 2 times for 15 seconds

  Scenario: A mirror given with a trailing slash still gives the provider mirror one slash
    When the terraform-init action runs in "infra" with "-input=false", with a terraform that fails unless given a provider mirror, and the mirror "https://storage.example/terraform/"
    Then terraform init ran 4 times, and the step passed
    And the last init's CLI configuration installs every provider from "https://storage.example/terraform/"

  Scenario: Without a provider mirror, an init that always fails stops after its tries
    When the terraform-init action runs in "infra" with "-input=false", with a terraform that fails unless given a provider mirror, and no mirror
    Then terraform init ran 3 times, and the step failed with 1

  Scenario: An init that the mirror cannot mend either fails the step
    When the terraform-init action runs in "." with "-input=false", with a terraform that always fails, and the mirror "https://storage.example/terraform"
    Then terraform init ran 4 times, and the step failed with 1

  Scenario: terraform-init's tries and pause are the caller's, and the default arguments are -input=false
    When the terraform-init action runs with its defaults but tries "2" and pause "0", with a terraform that always fails, and no mirror
    Then terraform init ran 2 times, and the step failed with 1
    And every init ran "-chdir=. init -input=false"
    And it slept 1 time for 0 seconds
