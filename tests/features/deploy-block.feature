Feature: pubkit sync --check checks pubkit.json's deploy block
  The deploy block names what a service needs before it deploys: its
  namespace, and maybe its host, deploy workflow, gate variable, overlay,
  and needs nothing implies. `pubkit sync --check` reads the block as it
  reads the others, and says what is wrong with it in one phrase per
  problem; then it runs the offline checks, so a block that disagrees with
  the manifests fails CI. A need type a package offers that pubkit cannot
  use is left out with a warning, and fails nothing.

  Background:
    Given the example service

  Scenario: A sound block passes sync --check, and says what it could not check
    Given the example service's managed files are in step
    When I run pubkit "sync" "--check"
    Then it succeeds
    And stderr mentions "pubkit: deploy: not checked, dns.site:example.example.eu: no hosting block says where example.example.eu is"

  Scenario: Without a need the manifests read, sync --check fails with the phrase that names it
    Given the example service's managed files are in step
    And the need "k8s.secret:example-app-env" is dropped
    When I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "pubkit: deploy: drift, k8s.secrets-declared:example: the manifests and the needs disagree about 1 Secret"
    And stderr mentions "Secret example-app-env is read by envFrom of Deployment/app container app, and no need says where it comes from: add it to deploy.needs, or a need's secret"

  Scenario: sync, writing, reports a disagreement and goes on
    Given the example service's managed files are in step
    And the need "k8s.secret:example-app-env" is dropped
    When I run pubkit "sync"
    Then it succeeds
    And stderr mentions "Secret example-app-env is read by envFrom of Deployment/app container app"

  Scenario: A bad block fails sync --check
    Given the example service's managed files are in step
    And the deploy block's "namespace" is "Example"
    When I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "pubkit: deploy: namespace must be a lower-case name of at most 31 characters"

  Scenario Outline: A key of the block that is missing or malformed is one named phrase
    Given the deploy block's "<key>" is <value>
    When I check the deploy block
    Then the only problem is "<phrase>"

    Examples:
      | key       | value                                    | phrase                                                                                       |
      | namespace | absent                                   | deploy: namespace is missing: the k3s namespace the service runs in                          |
      | namespace | "Example_NS"                             | deploy: namespace must be a lower-case name of at most 31 characters                         |
      | namespace | "a-namespace-far-longer-than-thirty-one" | deploy: namespace must be a lower-case name of at most 31 characters                         |
      | namespace | 7                                        | deploy: namespace must be a lower-case name of at most 31 characters                         |
      | host      | "Example.example.eu"                     | deploy: host must be a host name in lower case, such as example.example.eu                   |
      | workflow  | "deploy.sh"                              | deploy: workflow must be a workflow's file name, such as deploy.yml                          |
      | gate      | "deploy_example"                         | deploy: gate must be a variable's name: upper case letters, digits and _, not starting GITHUB_ |
      | gate      | "GITHUB_GATE"                            | deploy: gate must be a variable's name: upper case letters, digits and _, not starting GITHUB_ |
      | overlay   | "../elsewhere"                           | deploy: overlay must be a directory in the repository, such as k8s                           |
      | needs     | {"type": "upstream"}                     | deploy: needs must be a list of needs, each an object with a type                            |
      | needs     | [{"name": "storage"}]                    | deploy: needs[0]: type is missing                                                            |
      | needs     | ["upstream"]                             | deploy: needs[0] must be an object with a type                                               |
      | frob      | true                                     | deploy: frob is not a setting; it takes namespace, host, workflow, gate, overlay, units, providers and needs  |

  Scenario: The block must be an object
    Given the deploy block is []
    When I check the deploy block
    Then the only problem is "deploy must be an object, with namespace and maybe host, workflow, gate, overlay, units, providers and needs"

  Scenario: The workflow must be in .github/workflows
    Given the deploy block's "workflow" is "release.yml"
    When I check the deploy block
    Then the only problem is "deploy: workflow release.yml is not in .github/workflows"

  Scenario: The overlay must hold a kustomization
    Given the deploy block's "overlay" is "k8s/admin"
    When I check the deploy block
    Then the only problem is "deploy: overlay k8s/admin has no kustomization.yaml"

  Scenario: Without workflow and overlay, deploy.yml and k8s are meant, and must be there
    Given the deploy block's "workflow" is absent
    And the deploy block's "overlay" is absent
    And the file ".github/workflows/deploy.yml" is gone
    When I check the deploy block
    Then the only problem is "deploy: workflow deploy.yml is not in .github/workflows"

  Scenario: A need of a type pubkit does not know is named, with the types it knows
    Given the deploy block also needs {"type": "example.thing", "name": "one"}
    When I check the deploy block
    Then the only problem starts "deploy: needs[6]: example.thing is no type of need pubkit knows; it knows auth.sign-in-client, bitwarden.generated, bitwarden.secret,"

  Scenario: A key a need's type does not take is named, with the keys it takes
    Given the deploy block also needs {"type": "upstream", "name": "files", "health": "https://files.example.eu/healthz", "healthz": "/"}
    When I check the deploy block
    Then the only problem is "deploy: needs[6] (upstream:files): healthz is not a setting of upstream; it takes type, name, after, unit and health"

  Scenario: A need's own key that is malformed is named
    Given the deploy block also needs {"type": "upstream", "name": "files", "health": "http://files.example.eu/healthz"}
    When I check the deploy block
    Then the only problem is "deploy: needs[6] (upstream:files): health must be an https:// address"

  Scenario: A key a need's type requires is named
    Given the deploy block also needs {"type": "bitwarden.generated", "name": "example-key"}
    When I check the deploy block
    Then the only problem is "deploy: needs[6] (bitwarden.generated:example-key): shape is missing"

  Scenario: A need's secret is checked as one
    Given the deploy block also needs {"type": "bitwarden.secret", "name": "example-key", "secret": {"name": "Example", "decode": "hex"}}
    When I check the deploy block
    Then the problems are
      | problem                                                                                               |
      | deploy: needs[6] (bitwarden.secret:example-key): secret.name must be a Secret's name, in lower case    |
      | deploy: needs[6] (bitwarden.secret:example-key): secret.decode must be "base64"                       |

  Scenario: A need whose type has no default name must have one
    Given the deploy block also needs {"type": "upstream", "health": "https://files.example.eu/healthz"}
    When I check the deploy block
    Then the only problem is "deploy: needs[6] (upstream): it needs a name"

  Scenario: A need declared twice, two ways, is named
    Given the deploy block also needs {"type": "upstream", "name": "storage", "health": "https://other.example.eu/healthz"}
    When I check the deploy block
    Then the only problem is "deploy: needs[6]: upstream:storage is declared twice"

  Scenario: A need declared twice the same way is one
    Given the deploy block also needs {"type": "upstream", "name": "storage", "health": "https://storage.example.eu/healthz"}
    When I check the deploy block
    Then there is no problem
    And the plan has 1 "upstream:storage"

  Scenario: after must name a need of this deploy
    Given the need "service.health:example" is after "upstream:files"
    When I check the deploy block
    Then the only problem is "deploy: service.health:example is after upstream:files, which is no need of this deploy"

  Scenario: Needs that wait for each other are named, around the cycle
    Given the need "upstream:storage" is after "service.health:example"
    When I check the deploy block
    Then the only problem is "deploy: these needs wait for each other: deploy.rollout:example -> github.gate:example -> upstream:storage -> service.health:example -> deploy.rollout:example"

  Scenario: A k8s.manifests path must be a file
    Given the deploy block also needs {"type": "k8s.manifests", "path": "k8s/admin/gone.yaml"}
    When I check the deploy block
    Then the only problem is "deploy: needs[6] (k8s.manifests): k8s/admin/gone.yaml is not a file in the repository"

  Scenario Outline: Each type's name, when the need gives none
    Given the deploy block also needs <need>
    When I check the deploy block
    Then there is no problem
    And the plan has 1 "<id>"

    Examples:
      | need                                                                           | id                                      |
      | {"type": "k8s.namespace"}                                                      | k8s.namespace:example                   |
      | {"type": "k8s.secrets-declared"}                                               | k8s.secrets-declared:example            |
      | {"type": "k8s.manifests", "path": "k8s/app.yaml"}                              | k8s.manifests:k8s/app.yaml              |
      | {"type": "dns.site", "host": "other.example.eu"}                               | dns.site:other.example.eu               |
      | {"type": "github.deploy-settings"}                                             | github.deploy-settings:example          |
      | {"type": "github.gate", "variable": "DEPLOY_EXAMPLE"}                          | github.gate:example                     |
      | {"type": "deploy.rollout"}                                                     | deploy.rollout:example                  |
      | {"type": "k8s.secret", "name": "example-app-env"}                              | k8s.secret:example-app-env              |
      | {"type": "github.secret", "name": "EXAMPLE_MORE"}                              | github.secret:EXAMPLE_MORE              |
      | {"type": "github.variable", "name": "EXAMPLE_MORE"}                            | github.variable:EXAMPLE_MORE            |
      | {"type": "bitwarden.secret", "name": "example-cookie-secret"}                  | bitwarden.secret:example-cookie-secret  |

  Scenario: A need type a package offers that is not one is left out, and sync --check passes
    Given the example service's managed files are in step
    And the package "example-needs" offers the need type "example.bad", whose schema takes any key
    When I run pubkit "sync" "--check"
    Then it succeeds
    And stderr mentions "pubkit: warning: sustenaut.needs entry point bad (example_needs:BAD, from example-needs) is left out: its schema must be an object's, with additionalProperties false"

  Scenario: A need type a package offers is listed, with where it came from
    Given the package "example-needs" offers the need type "example.thing"
    When I run pubkit "deploy" "types"
    Then it succeeds
    And the listing shows "example.thing" graded "none", from "example-needs", taking "size?"
    And the listing shows "k8s.secret" graded "auto", from "pubkit", taking "keys?, shapes?"
    And it prints "20 types; every need also takes type, name, after and unit"

  Scenario: A package offering a type pubkit has is left out, naming both
    Given the package "example-needs" offers the need type "k8s.secret"
    When I run pubkit "deploy" "types"
    Then it succeeds
    And stderr mentions "sustenaut.needs entry point k8s.secret (example_needs:K8S_SECRET, from example-needs) offers k8s.secret, which pubkit offers too; example-needs's is left out"
    And it prints "19 types"

  Scenario Outline: A need type a package offers is checked as it loads
    Given the package "example-needs" offers a need type whose <part> is <value>
    When I run pubkit "deploy" "types"
    Then it succeeds
    And stderr mentions "<warning>"

    Examples:
      | part   | value         | warning                                                                     |
      | type   | "Thing"       | its type must be dotted lower-case words, such as example.thing             |
      | budget | 11            | its budget must be more than 0 and at most 10 seconds                       |
      | budget | 0             | its budget must be more than 0 and at most 10 seconds                       |
      | grade  | "sometimes"   | its grade must be one of auto, propose, human, none                         |
      | offline | "no"         | its offline must be true or false                                           |
      | check  | null          | it has no check                                                             |
      | module | "example_gone" | (example_gone:THING, from example-needs) does not load (ModuleNotFoundError), and is left out |

  Scenario: deploy check stops on a bad block, with exit code 2
    Given the deploy block's "gate" is "off"
    When I run pubkit "deploy" "check"
    Then the exit code is 2
    And stderr mentions "pubkit: deploy: gate must be a variable's name"

  Scenario: deploy check without a deploy block
    Given the deploy block is absent
    When I run pubkit "deploy" "check"
    Then the exit code is 2
    And stderr mentions "pubkit: deploy: pubkit.json has no deploy block"
