Feature: pubkit deploy check --offline checks what needs no network
  Offline, the walk renders the overlay and checks that the manifests and
  the needs agree (k8s.secrets-declared): every Secret the manifests read
  is one a need fills, with the keys they read, and every Secret a need
  fills is read. It parses the manifest files needs name, and finds the
  host in the hosting block. Every other need is "not checked offline", and
  the exit code counts only the offline ones.

  Background:
    Given the example service

  Scenario: The manifests and the needs agree
    When I run pubkit "deploy" "check" "--offline"
    Then it succeeds
    And it prints "ready   k8s.secrets-declared:example  the manifests read 3 Secrets, and a need says where each comes from"
    And it prints "ready   k8s.manifests:k8s/admin/network-policy.yaml  k8s/admin/network-policy.yaml holds 1 object"
    And it prints "ready   dns.site:example.example.eu  example.example.eu is a site of example.eu"
    And it prints "unknown k8s.namespace:example  not checked offline"
    And it prints "deploy: 18 needs, 3 ready, 0 missing, 0 drift, 0 blocked, 15 unknown"
    And nothing reached kubectl, GitHub or the network

  Scenario: A Secret the manifests read that no need fills
    Given the need "k8s.secret:example-app-env" is dropped
    When I run pubkit "deploy" "check" "--offline"
    Then the exit code is 1
    And it prints "drift   k8s.secrets-declared:example  the manifests and the needs disagree about 1 Secret"
    And it prints "Secret example-app-env is read by envFrom of Deployment/app container app, and no need says where it comes from: add it to deploy.needs, or a need's secret"

  Scenario: A Secret every reader of which is named, when no need fills it
    Given the need "bitwarden.secret:example-files-token" is dropped
    When I run pubkit "deploy" "check" "--offline"
    Then the exit code is 1
    And it prints "Secret example-files is read by volume files of Deployment/app, env TOKEN of CronJob/worker container worker, and no need says where it comes from"

  Scenario: A Secret a need fills that nothing reads
    Given the deploy block also needs {"type": "bitwarden.secret", "name": "example-old", "secret": {"name": "example-old"}}
    When I run pubkit "deploy" "check" "--offline"
    Then the exit code is 1
    And it prints "Secret example-old is declared by bitwarden.secret:example-old but nothing reads it"

  Scenario: A k8s.secret need that nothing reads
    Given the deploy block also needs {"type": "k8s.secret", "name": "example-oidc", "keys": ["client-id"]}
    When I run pubkit "deploy" "check" "--offline"
    Then the exit code is 1
    And it prints "Secret example-oidc is declared by k8s.secret:example-oidc but nothing reads it"

  Scenario: A key the manifests read that the need filling the Secret does not provide
    Given the need "bitwarden.secret:example-files-token" fills the key "other"
    When I run pubkit "deploy" "check" "--offline"
    Then the exit code is 1
    And it prints "Secret example-files key token is read by volume files of Deployment/app, and bitwarden.secret:example-files-token provides only other"

  Scenario: A need's secret without a key provides any key
    Given the need "bitwarden.secret:example-files-token" fills no key
    When I run pubkit "deploy" "check" "--offline"
    Then it succeeds

  Scenario: A reference marked optional needs no need
    Given every reference to "example-app-env" in the manifests is optional
    And the need "k8s.secret:example-app-env" is dropped
    When I run pubkit "deploy" "check" "--offline"
    Then it succeeds
    And it prints "the manifests read 2 Secrets"

  Scenario: A Secret the manifests hold themselves, as a secretGenerator makes it, counts as provided
    Given the rendered manifests hold the Secret "example-app-env"
    And the need "k8s.secret:example-app-env" is dropped
    When I run pubkit "deploy" "check" "--offline"
    Then it succeeds

  Scenario: An overlay that does not render is drift, with what kustomize said
    Given kustomize fails with "accumulating resources: missing.yaml"
    When I run pubkit "deploy" "check" "--offline"
    Then the exit code is 1
    And it prints "drift   k8s.secrets-declared:example  the overlay does not render"
    And it prints "kustomize build k8s failed: accumulating resources: missing.yaml"

  Scenario: Without kustomize the agreement is not checked, and fails --offline
    Given neither kustomize nor kubectl is installed
    When I run pubkit "deploy" "check" "--offline"
    Then the exit code is 1
    And it prints "unknown k8s.secrets-declared:example  the overlay could not be rendered"

  Scenario: A manifest file that holds no object is drift
    Given the file "k8s/admin/network-policy.yaml" holds only a comment
    When I run pubkit "deploy" "check" "--offline"
    Then the exit code is 1
    And it prints "drift   k8s.manifests:k8s/admin/network-policy.yaml  k8s/admin/network-policy.yaml holds no object"

  Scenario: A manifest file that is not YAML is drift
    Given the file "k8s/admin/network-policy.yaml" holds "{ not: yaml"
    When I run pubkit "deploy" "check" "--offline"
    Then the exit code is 1
    And it prints "drift   k8s.manifests:k8s/admin/network-policy.yaml  k8s/admin/network-policy.yaml cannot be read as manifests"

  Scenario: Without a hosting block the host is not checked, and --offline fails
    Given no hosting block keeps the zone
    When I run pubkit "deploy" "check" "--offline"
    Then the exit code is 1
    And it prints "unknown dns.site:example.example.eu  no hosting block says where example.example.eu is"
    And it prints "give --zones PATH, the pubkit.json whose hosting block keeps the zone"

  Scenario: A host that is no site of the zone is missing
    Given the deploy block's "host" is "other.example.eu"
    When I run pubkit "deploy" "check" "--offline"
    Then the exit code is 1
    And it prints "missing dns.site:other.example.eu  other.example.eu is no site of a zone in the hosting block"

  Scenario: --offline exits 0 when the offline needs are ready, whatever the online ones would find
    Given kubectl may not read anything
    And GitHub refuses every request
    And nothing answers on the network
    When I run pubkit "deploy" "check" "--offline"
    Then it succeeds
    And nothing reached kubectl, GitHub or the network

  Scenario: --json prints a journal document, with no need's params
    When I run pubkit "deploy" "check" "--offline" "--json"
    Then it succeeds
    And the journal is a deploy-journal/2 document
    And the journal's walk is offline, from "laptop"
    And the journal lists the 18 needs in walk order, none with params
    And the journal counts 3 ready and 15 unknown
