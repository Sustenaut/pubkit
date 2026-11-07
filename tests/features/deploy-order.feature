Feature: The needs a deploy block implies, and the order they are walked in
  Beside the needs it lists, a deploy block implies others: from its
  namespace, host and gate, from the secrets, github_app and variables
  blocks, from each need's secret, and from the Secrets the rendered
  manifests read. They are walked each after the needs it waits for, by
  rules pubkit has and by each need's after, ties broken by id, so the
  order is the same on every run.

  Background:
    Given the example service

  Scenario: The example service's needs, and where each came from
    When I load the plan
    Then its needs are
      | id                                            | implied by                                             |
      | bitwarden.generated:example-cookie-secret     |                                                        |
      | bitwarden.secret:example-files-token          |                                                        |
      | k8s.secret:example-app-env                    |                                                        |
      | k8s.manifests:k8s/admin/network-policy.yaml   |                                                        |
      | upstream:storage                              |                                                        |
      | service.health:example                        |                                                        |
      | k8s.namespace:example                         | namespace                                              |
      | deploy.rollout:example                        | namespace                                              |
      | k8s.secrets-declared:example                  | namespace                                              |
      | dns.site:example.example.eu                   | host                                                   |
      | github.gate:example                           | gate                                                   |
      | github.secret:EXAMPLE_TOKEN                   | secrets                                                |
      | github.variable:EXAMPLE_USER                  | secrets                                                |
      | github.secret:server/SUSTENAUT_DEPLOY_SSH_KEY | secrets                                                |
      | github.deploy-settings:example                | secrets                                                |
      | github.variable:EXAMPLE_SETTING               | variables                                              |
      | k8s.secret:example-page-secrets               | secret of bitwarden.generated:example-cookie-secret, manifests |
      | k8s.secret:example-files                      | secret of bitwarden.secret:example-files-token, manifests      |

  Scenario: The walk order is the documented one
    When I load the plan
    Then the walk order is
      | id                                            |
      | bitwarden.generated:example-cookie-secret     |
      | bitwarden.secret:example-files-token          |
      | dns.site:example.example.eu                   |
      | github.deploy-settings:example                |
      | github.secret:EXAMPLE_TOKEN                   |
      | github.secret:server/SUSTENAUT_DEPLOY_SSH_KEY |
      | github.variable:EXAMPLE_SETTING               |
      | github.variable:EXAMPLE_USER                  |
      | k8s.namespace:example                         |
      | k8s.manifests:k8s/admin/network-policy.yaml   |
      | k8s.secret:example-app-env                    |
      | k8s.secret:example-files                      |
      | k8s.secret:example-page-secrets               |
      | k8s.secrets-declared:example                  |
      | upstream:storage                              |
      | github.gate:example                           |
      | deploy.rollout:example                        |
      | service.health:example                        |

  Scenario: The order does not depend on the order the needs are listed in
    Given the deploy block lists its needs the other way round
    When I load the plan
    Then the walk order is the example's

  Scenario: A Secret a need's secret fills, and the manifests read, is one need with the keys of both
    When I load the plan
    Then "k8s.secret:example-page-secrets" has the keys "cookie-secret" and the shape "oauth2-proxy-cookie" for "cookie-secret"
    And "k8s.secret:example-page-secrets" comes after "bitwarden.generated:example-cookie-secret"
    And "k8s.secret:example-page-secrets" comes after "k8s.namespace:example"

  Scenario: after is honoured
    Given the need "bitwarden.secret:example-files-token" is after "upstream:storage"
    When I load the plan
    Then "bitwarden.secret:example-files-token" comes after "upstream:storage"
    And "k8s.secret:example-files" comes after "bitwarden.secret:example-files-token"

  Scenario: Without a gate, the rollout waits for everything, and the health for the rollout
    Given the deploy block's "gate" is absent
    When I load the plan
    Then the plan has no "github.gate:example"
    And "deploy.rollout:example" comes after "upstream:storage"
    And "deploy.rollout:example" comes after "k8s.secrets-declared:example"
    And "service.health:example" comes after "deploy.rollout:example"

  Scenario: Without a host there is no dns.site, and no implied health
    Given the deploy block's "host" is absent
    And the need "service.health:example" is dropped
    When I load the plan
    Then the plan has no "dns.site:example.example.eu"
    And the plan has no "service.health:example"

  Scenario: A GitHub App's slots are needs of their own
    Given pubkit.json's github_app block names the app "example-app"
    When I load the plan
    Then its needs include
      | id                                     | implied by |
      | github.variable:EXAMPLE_APP_ID         | github_app |
      | github.variable:EXAMPLE_APP_INSTALLATION_ID | github_app |
      | github.secret:EXAMPLE_APP_KEY          | github_app |

  Scenario: Without a server's setting in the secrets block, there is no deploy-settings need
    Given the secrets block sends only "EXAMPLE_TOKEN"
    When I load the plan
    Then the plan has no "github.deploy-settings:example"

  Scenario: --namespace names the singletons and the k8s needs by another namespace
    When I load the plan for the namespace "example-two"
    Then the plan has 1 "k8s.namespace:example-two"
    And the plan has 1 "service.health:example-two"
    And the plan has no "k8s.namespace:example"

  Scenario: A Secret every reference to which is optional is no need
    When I load the plan
    Then the plan has no "k8s.secret:example-extra"

  Scenario: Without the manifests, the plan has what the block implies
    Given the overlay does not render
    When I load the plan
    Then the plan has 1 "k8s.secret:example-page-secrets"
    And the need "k8s.secret:example-page-secrets" was implied by "secret of bitwarden.generated:example-cookie-secret"
