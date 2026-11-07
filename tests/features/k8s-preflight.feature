Feature: pubkit k8s preflight checks the Secrets a deploy reads before anything is applied
  `pubkit k8s preflight` reads rendered manifests, collects every Secret
  their pods read (env, envFrom, secret and projected volumes,
  imagePullSecrets, of containers and init containers) and reads each one
  through kubectl: it must be there with every key named, unless the
  reference is optional, and a value of a known shape (oauth2-proxy's
  cookie secret) must have it. It fails in seconds, naming the Secret, the
  key and what reads it, and never prints a value. `pubkit k8s diagnose`
  shows why the pods of a failed rollout are not ready.

  Background:
    Given the sample manifests

  Scenario: Every Secret is there, with its keys
    Given the namespace "example" holds every Secret the sample reads
    When I preflight the sample in "example"
    Then it succeeds
    And it prints "ok: Secret page-secrets in example, keys client-secret, cookie-secret"
    And it prints "ok: Secret held-config in example, keys a"
    And it prints "Secret extra is not in example, and every reference to it is optional"
    And it prints "preflight: the manifests read 7 Secrets, and each is as they need"
    And kubectl read each Secret once, and not held-config, which the manifests hold

  Scenario: A missing Secret is named, with what reads it
    Given the namespace "example" holds every Secret the sample reads but "app-files"
    When I preflight the sample in "example"
    Then it fails
    And it prints "error: Secret app-files is not in namespace example; volume files of Deployment/app read it"
    And stderr mentions "preflight: 1 problem with the Secrets in example"

  Scenario: A missing key is named, with what reads it
    Given the namespace "example" holds every Secret the sample reads
    And the Secret "page-secrets" has no key "client-secret"
    When I preflight the sample in "example"
    Then it fails
    And it prints "error: Secret page-secrets has no key client-secret, which env OAUTH2_PROXY_CLIENT_SECRET of Deployment/proxy container oauth2-proxy reads"

  Scenario: A key a projected volume's items name in an init container's pod must be there
    Given the namespace "example" holds every Secret the sample reads
    And the Secret "backup-key" has no key "key.json"
    When I preflight the sample in "example"
    Then it fails
    And it prints "error: Secret backup-key has no key key.json, which volume credentials of CronJob/backup reads"

  Scenario: An image pull Secret must be there
    Given the namespace "example" holds every Secret the sample reads but "ghcr-pull"
    When I preflight the sample in "example"
    Then it fails
    And it prints "error: Secret ghcr-pull is not in namespace example; imagePullSecrets of Deployment/app read it"

  Scenario: A cookie secret in standard base64 is named as the likely mistake, and not printed
    Given the namespace "example" holds every Secret the sample reads
    And the cookie secret is "standard base64 of 32 bytes"
    When I preflight the sample in "example"
    Then it fails
    And it prints "error: Secret page-secrets key cookie-secret (env OAUTH2_PROXY_COOKIE_SECRET of Deployment/proxy container oauth2-proxy), oauth2-proxy's cookie secret: 44 bytes: it is standard base64 (with + or /) of 32 bytes"
    And it prints "make it URL-safe, + as - and / as _"
    And no value of a Secret is printed

  Scenario Outline: A cookie secret oauth2-proxy takes passes
    Given the namespace "example" holds every Secret the sample reads
    And the cookie secret is "<value>"
    When I preflight the sample in "example"
    Then it succeeds

    Examples:
      | value                                 |
      | URL-safe base64 of 32 bytes           |
      | URL-safe base64 of 16 bytes, unpadded |
      | 32 bytes as they are                  |
      | 24 bytes as they are                  |

  Scenario Outline: A cookie secret oauth2-proxy refuses fails, by its length alone
    Given the namespace "example" holds every Secret the sample reads
    And the cookie secret is "<value>"
    When I preflight the sample in "example"
    Then it fails
    And it prints "<says>"
    And no value of a Secret is printed

    Examples:
      | value                                    | says                                                    |
      | 20 bytes as they are                     | 20 bytes, 15 after URL-safe base64 decoding; oauth2-proxy needs 16, 24 or 32 bytes |
      | URL-safe base64 of 20 bytes              | 28 bytes, 20 after URL-safe base64 decoding             |
      | URL-safe base64 of 32 bytes, and a newline | 45 bytes, with white space around it                  |

  Scenario: A cookie secret an envFrom Secret holds is checked under its env name
    Given the namespace "example" holds every Secret the sample reads
    And the Secret "app-env" holds OAUTH2_PROXY_COOKIE_SECRET as "standard base64 of 32 bytes"
    When I preflight the sample in "example"
    Then it fails
    And it prints "error: Secret app-env key OAUTH2_PROXY_COOKIE_SECRET (envFrom of Deployment/app container app, as OAUTH2_PROXY_COOKIE_SECRET)"

  Scenario: oauth2-proxy's --cookie-secret flag is checked, written in or from an env variable
    Given the namespace "example" holds every Secret the sample reads
    And the proxy is given --cookie-secret "standard base64 of 32 bytes" as it is
    When I preflight the sample offline
    Then it fails
    And it prints "error: --cookie-secret of Deployment/proxy container oauth2-proxy, oauth2-proxy's cookie secret: 44 bytes: it is standard base64"
    And no value of a Secret is printed

  Scenario: --cookie-secret=$(VAR) checks the Secret behind VAR
    Given the namespace "example" holds every Secret the sample reads
    And the proxy is given --cookie-secret=$(SIGNING) from the Secret "page-secrets" key "signing"
    And the Secret "page-secrets" holds "signing" as "20 bytes as they are"
    When I preflight the sample in "example"
    Then it fails
    And it prints "error: Secret page-secrets key signing (env SIGNING of Deployment/proxy container oauth2-proxy), oauth2-proxy's cookie secret: 20 bytes"

  Scenario: A Secret kubectl may not read fails with what the deploy identity needs
    Given kubectl may not read Secrets
    When I preflight the sample in "example"
    Then it fails
    And it prints "kubectl may not read Secrets in example: the deploy identity needs get on secrets there"

  Scenario: Offline, it lists what the manifests need and reads no cluster
    When I preflight the sample offline
    Then it succeeds
    And it prints "not read (offline), the manifests need the Secrets app-env, app-files (token), backup-key (key.json), extra (x), ghcr-pull, page-secrets (client-secret, cookie-secret)"
    And it prints "ok: Secret held-config in example, keys a"
    And kubectl was not run

  Scenario: A reference that names no key fails before kubectl is asked
    Given the proxy's client secret names no key
    When I preflight the sample offline
    Then it fails
    And it prints "error: env OAUTH2_PROXY_CLIENT_SECRET of Deployment/proxy container oauth2-proxy names no key of its Secret"

  Scenario: The preflight runs from its file alone, without pubkit installed
    When I run the preflight's file alone, offline
    Then it succeeds
    And it prints "not read (offline), the manifests need the Secrets"

  Scenario: diagnose shows a crash-looping pod's state, events and logs, and skips ready pods
    Given the pod "proxy-1" crash-loops, its container "oauth2-proxy" exiting 1 with "cookie_secret must be 16, 24, or 32 bytes"
    And the pod "app-1" is ready
    When I diagnose "example"
    Then it succeeds
    And it prints "--- pod proxy-1: Running"
    And it prints "container oauth2-proxy: waiting (CrashLoopBackOff back-off 40s), 3 restarts, last exit 1 Error"
    And it prints "event BackOff: Back-off restarting failed container"
    And it prints "logs of oauth2-proxy (previous):"
    And it prints "    cookie_secret must be 16, 24, or 32 bytes"
    And it does not print "app-1"

  Scenario: diagnose says so when every pod is ready
    Given the pod "app-1" is ready
    When I diagnose "example"
    Then it succeeds
    And it prints "diagnose: every pod in example is ready and none restarted"
