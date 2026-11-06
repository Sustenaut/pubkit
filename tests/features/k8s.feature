Feature: Services deploy to k3s through one workflow and one kustomize component
  k8s-deploy.yml builds a service's image, renders its overlays with it and
  sends each to the server's forced command over deploy-ssh. The component
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
