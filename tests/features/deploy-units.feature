Feature: A deploy block's units, each walked and journaled on its own
  The top level is the unit its namespace names; `units` adds more, each
  with its own namespace, overlay, gate and host, sharing the workflow and
  every need with no `unit`. When the workflow calls k8s-deploy.yml, its
  deploys lines and the units must agree, and from 0.31.0 it must start on
  workflow_run of main, with actions: read.

  Background:
    Given the example service
    And the deploy block has the unit "other", in "example-other", from "k8s-other", gated by "DEPLOY_OTHER"

  Scenario: A unit's plan has its own namespace and gate, the shared needs, and those scoped to it
    Given the deploy block also needs {"type": "k8s.secret", "name": "example-other-env", "unit": "other"}
    When I load the plan for the unit "other"
    Then its needs include
      | id                                   | implied_by |
      | k8s.namespace:example-other          | namespace  |
      | github.gate:example-other            | gate       |
      | k8s.secret:example-other-env         |            |
      | upstream:storage                     |            |
      | github.secret:EXAMPLE_TOKEN          | secrets    |
    And the plan has no "dns.site:example.example.eu"
    And the plan has no "github.gate:example"

  Scenario: A need scoped to another unit is not the top level's
    Given the deploy block also needs {"type": "k8s.secret", "name": "example-other-env", "unit": "other"}
    When I load the plan
    Then the plan has no "k8s.secret:example-other-env"

  Scenario: Without --unit every unit is walked, each its own journal
    When I run pubkit "deploy" "walk" "--json"
    Then the exit code is 1
    And the journals are of the units "example, other"
    And the journal of "other" says "k8s.namespace:example-other" is missing

  Scenario: --unit walks one
    When I run pubkit "deploy" "walk" "--unit" "other" "--json"
    Then the journals are of the units "other"

  Scenario: A unit the block does not have is refused
    When I run pubkit "deploy" "walk" "--unit" "nowhere"
    Then the exit code is 2
    And stderr mentions "--unit nowhere is no unit of the deploy block; it has example, other"

  Scenario: A need naming no unit is a problem
    Given the deploy block also needs {"type": "upstream", "name": "files", "health": "https://files.example.eu/", "unit": "nowhere"}
    When I check the deploy block
    Then the only problem is "deploy: needs[6]: unit nowhere is no unit of the block; it has example and other"

  Scenario: A unit's overlay must hold a kustomization
    Given the file "k8s-other/kustomization.yaml" is gone
    When I check the deploy block
    Then the only problem is "deploy: overlay k8s-other (unit other) has no kustomization.yaml"

  Scenario: The deploys lines and the units agree
    Given the deploy workflow calls k8s-deploy.yml "v0.31.1" deploying "k8s example DEPLOY_EXAMPLE; k8s-other example-other DEPLOY_OTHER"
    When I check the workflow against the units
    Then there is no problem

  Scenario: A unit without its deploys line, and a line without its unit, are both named
    Given the deploy workflow calls k8s-deploy.yml "v0.31.1" deploying "k8s example; k8s-third example-third"
    When I check the workflow against the units
    Then the problems are
      | problem |
      | deploy: unit other (k8s-other, namespace example-other) is no deploys line of deploy.yml: add "k8s-other example-other" to its deploys |
      | deploy: deploy.yml's deploys line "k8s-third example-third" is no unit of the deploy block: add it to deploy.units, or drop the line |

  Scenario: sync --check fails when they disagree
    Given the example service's managed files are in step
    And the deploy workflow calls k8s-deploy.yml "v0.31.1" deploying "k8s example"
    When I run pubkit "sync" "--check"
    Then the exit code is 1
    And stderr mentions "deploy: unit other (k8s-other, namespace example-other) is no deploys line of deploy.yml"

  Scenario: From k8s-deploy.yml 0.31.0, a deploy started by push names the two lines to change
    Given the deploy workflow calls k8s-deploy.yml "v0.31.1" deploying "k8s example; k8s-other example-other", on push
    When I check the workflow against the units
    Then the only problem starts "deploy: deploy.yml calls k8s-deploy.yml 0.31.1, which deploys what a run of main tested: under on, have"
    And the problem names "workflow_run: { workflows: [main], types: [completed], branches: [main] }" and "permissions: { actions: read, contents: read, packages: write }"

  Scenario: Before 0.31.0 a deploy started by push is as it was
    Given the deploy workflow calls k8s-deploy.yml "v0.30.0" deploying "k8s example; k8s-other example-other", on push
    When I check the workflow against the units
    Then there is no problem
