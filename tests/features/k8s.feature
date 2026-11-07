Feature: Services deploy to k3s through one workflow and one kustomize component
  k8s-deploy.yml waits until the workflows it requires have passed on a
  commit, builds a service's image of it, renders its overlays with it and
  sends each to the server's forced command over deploy-ssh, unless the
  default branch has moved on from that commit. The component
  k8s/components/app hardens a service's app and gives it its probes, so a
  service's own manifests hold only what is its own.

  Scenario: The component hardens the app and gives it its probes
    When I render the sample service with kustomize
    Then the Deployment "app" runs as user 1000, not root, under the runtime's seccomp profile
    And its container "app" can't gain privileges, has no capabilities and can't write its root filesystem
    And its container "app" starts on /healthz, lives on /livez and is ready on /healthz, on the port "http"
    And the Deployment "app" keeps its own fsGroup

  Scenario: The component leaves every other Deployment alone
    When I render the sample service with kustomize
    Then the Deployment "proxy" has no securityContext and no probes

  Scenario: A service's own patch wins over the component
    When I render the sample service with kustomize
    Then the Deployment "app" mounts its service account token

  Scenario: k8s-deploy pushes the image it checked, and deploys only to a server it is given
    When I parse the workflow "k8s-deploy.yml"
    Then its build job builds with docker-build, runs the check, then pushes to ghcr.io
    And its deploy job runs once per unit, in the environment the caller names, one run at a time per unit
    And it deploys only when SUSTENAUT_SERVER is set and the unit's variable, if any, is on
    And it deploys with deploy-ssh, the unit as the command and the rendered manifests as input, over the tailnet

  Scenario: k8s-deploy deploys a commit only after the workflows it requires passed on it
    When I parse the workflow "k8s-deploy.yml"
    Then its ready job runs for a workflow_run only when that run passed on a push to the default branch
    And its build and deploy jobs need ready, and check out, tag and deploy the commit ready gives
    And its ready job may read the repository's runs, and waits for main and, where there is one, integrations

  Scenario: ready gives the commit once every workflow it requires passed on it
    Given the repository's workflows are "main, integrations, deploy"
    And on the commit "main" passed and "integrations" passed
    When ready runs for the commit
    Then it succeeds
    And it prints "main passed on"
    And it prints "integrations passed on"
    And ready gives the commit

  Scenario: ready waits for a workflow still running
    Given the repository's workflows are "main, integrations, deploy"
    And on the commit "main" is running and "integrations" passed
    And then "main" passed and "integrations" passed
    When ready runs for the commit
    Then it succeeds
    And it prints "Waiting for main on"
    And ready gives the commit

  Scenario: ready fails, naming the workflow, when one did not pass
    Given the repository's workflows are "main, integrations, deploy"
    And on the commit "main" failed and "integrations" passed
    When ready runs for the commit
    Then it fails
    And it prints "main did not pass on"
    And it prints "(failure): nothing deploys"
    And ready gives no commit

  Scenario: ready skips an optional workflow the repository lacks, and fails on a required one
    Given the repository's workflows are "main, deploy"
    And on the commit "main" passed and "integrations" passed
    When ready runs for the commit
    Then it succeeds
    And it prints "has no workflow named integrations, so it is not waited for"
    When ready runs for the commit, requiring "main lint"
    Then it fails
    And it prints "require: example/service has no workflow named lint"

  Scenario: ready fails when a workflow it requires never ran on the commit
    Given the repository's workflows are "main, integrations, deploy"
    And on the commit only "integrations" passed
    When ready runs for the commit
    Then it fails
    And it prints "main has not run on a push of"

  Scenario: A commit the default branch has moved on from is not deployed, unless by hand
    Given the default branch has moved on from the commit
    When the deploy gate runs for a "workflow_run"
    Then it succeeds
    And it prints "has moved on from"
    And the gate does not deploy
    When the deploy gate runs for a "workflow_dispatch"
    Then the gate deploys
