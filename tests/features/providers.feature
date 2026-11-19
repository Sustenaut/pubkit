Feature: What sync and bump do for a deploy, now that the walk is pubdeploy's
  The managed with-secrets in every repository with a with-secrets.json, the
  launchers' provider wheels, pubsec and pubdeploy in deploy.providers,
  ./pubdeploy, and the sustenaut.sync and sustenaut.bump hooks, in a scratch
  repository with the hooks faked. pubrules runs in this process, so the
  hooks it loads are the fakes; a provider's "release" in deploy.providers is
  its pin at the release the scenario's release state lists.

  Scenario: A utility with a with secrets json gets the managed with secrets
    Given a "utility" repository
    And a file "with-secrets.json" holding "{"project": "example"}"
    And a file "with-secrets" holding:
      """
      #!/usr/bin/env bash
      exec bws run -- "$@"
      """
    When I call pubrules with "sync --check"
    Then the exit code is 1
    And stderr mentions "with-secrets differs from pubrules"
    And stderr mentions "it is an older script, which does not say which Bitwarden project it read (PUBRULES_SECRETS_PROJECT), so pubdeploy finds every bitwarden.* need unknown through it"
    When I call pubrules with "sync"
    Then it succeeds
    And the file "with-secrets" includes "Managed by pubrules"
    And the file "with-secrets" can be run
    When I call pubrules with "sync --check"
    Then it succeeds

  Scenario: A utility without one gets none
    Given a "utility" repository
    When I call pubrules with "sync"
    Then it succeeds
    And the file "with-secrets" does not exist

  Scenario: The launcher runs with each provider checked against its sha256
    Given a scratch directory
    And a stand-in "uvx"
    And the environment sets "XDG_CACHE_HOME" to "cache"
    And pubrules renders the utility launcher for '{"deploy": {"providers": [{"distribution": "example-needs", "wheel": "https://github.com/example-org/example-needs/releases/download/v1.0.0/example_needs-1.0.0-py3-none-any.whl", "sha256": "c8d27a58c983d63f54eb1a984251ffd58f5c3e993f28582340275e5b1a26a2c0"}]}}'
    And a file "cache/pubrules/providers/c8d27a58c983d63f54eb1a984251ffd58f5c3e993f28582340275e5b1a26a2c0/example_needs-1.0.0-py3-none-any.whl" holding "a wheel"
    When I run "bash pubrules --version"
    Then it succeeds
    And the file "pubrules" includes "provider https://github.com/example-org/example-needs/releases/download/v1.0.0/example_needs-1.0.0-py3-none-any.whl c8d27a58c983d63f54eb1a984251ffd58f5c3e993f28582340275e5b1a26a2c0"
    And the file "pubrules" includes ""${with[@]}" pubrules "$@""
    And "uvx" was run with arguments matching "--from * --with cache/pubrules/providers/c8d27a58c983d63f54eb1a984251ffd58f5c3e993f28582340275e5b1a26a2c0/example_needs-1.0.0-py3-none-any.whl pubrules --version"
    When the file "cache/pubrules/providers/c8d27a58c983d63f54eb1a984251ffd58f5c3e993f28582340275e5b1a26a2c0/example_needs-1.0.0-py3-none-any.whl" changes to:
      """
      another wheel
      """
    And I run "bash pubrules --version"
    Then the exit code is 1
    And stderr mentions "is not the wheel deploy.providers pins"
    And the file "cache/pubrules/providers/c8d27a58c983d63f54eb1a984251ffd58f5c3e993f28582340275e5b1a26a2c0/example_needs-1.0.0-py3-none-any.whl" does not exist
    And "uvx" was run 1 times

  Scenario: The launcher without providers is as it was
    Given a scratch directory
    When pubrules renders the utility launcher for '{}'
    Then the file "pubrules" does not include "provider"
    And the file "pubrules" includes "py3-none-any.whl" pubrules "$@""

  Scenario: Sync lists pubsec and pubdeploy in deploy providers and check fails without them
    Given a repository with a deploy block
    When I call pubrules with "sync --check"
    Then the exit code is 1
    And stderr mentions "pubrules: deploy.providers does not list pubsec: the types that hold a secret's value are pubsec's"
    And stderr mentions "pubrules: deploy.providers does not list pubdeploy: the deploy walk, and the check sync runs of the block, are pubdeploy's"
    And nothing was written
    When I call pubrules with "sync"
    Then it succeeds
    And pubrules.json is as it was but for deploy.providers, which is:
      """
      [{"release": "pubdeploy"}, {"release": "pubsec"}]
      """
    When I call pubrules with "sync --check"
    Then it succeeds

  Scenario: Sync writes pubsec and with secrets runs it
    Given a repository with a deploy block whose deploy.providers is:
      """
      [{"distribution": "pubdeploy", "wheel": "https://github.com/example-org/pubdeploy/releases/download/v9.0.0/pubdeploy-9.0.0-py3-none-any.whl", "sha256": "fe7bb78d115e16240d35e7990fc029399a1d2023d77541fb1cee658edac4b081"},
       {"distribution": "pubsec", "wheel": "https://github.com/example-org/pubsec/releases/download/v9.0.0/pubsec-9.0.0-py3-none-any.whl", "sha256": "c71bff738d91630386010f388ac9252cc2c1926f0e3753242dec9c136c75272a"}]
      """
    When I call pubrules with "sync"
    Then it succeeds
    And the file "pubsec" includes "fetch https://github.com/example-org/pubsec/releases/download/v9.0.0/pubsec-9.0.0-py3-none-any.whl c71bff738d91630386010f388ac9252cc2c1926f0e3753242dec9c136c75272a providers deploy.providers"
    And the file "pubsec" includes "exec uvx --from "$fetched" pubsec "$@""
    And the file "pubsec" can be run
    And the file "with-secrets" ends with "exec "$(cd "$(dirname "$0")" && pwd)/pubsec" run --cwd "$PWD" -- "$@""
    And the file "pubrules" includes "provider https://github.com/example-org/pubsec/releases/download/v9.0.0/pubsec-9.0.0-py3-none-any.whl c71bff738d91630386010f388ac9252cc2c1926f0e3753242dec9c136c75272a"
    And the file "pubrules" includes "provider https://github.com/example-org/pubdeploy/releases/download/v9.0.0/pubdeploy-9.0.0-py3-none-any.whl fe7bb78d115e16240d35e7990fc029399a1d2023d77541fb1cee658edac4b081"

  Scenario: Sync writes pubdeploy which runs with the other providers
    Given a repository with a deploy block whose deploy.providers is:
      """
      [{"distribution": "pubdeploy", "wheel": "https://github.com/example-org/pubdeploy/releases/download/v9.0.0/pubdeploy-9.0.0-py3-none-any.whl", "sha256": "fe7bb78d115e16240d35e7990fc029399a1d2023d77541fb1cee658edac4b081"},
       {"distribution": "pubsec", "wheel": "https://github.com/example-org/pubsec/releases/download/v9.0.0/pubsec-9.0.0-py3-none-any.whl", "sha256": "c71bff738d91630386010f388ac9252cc2c1926f0e3753242dec9c136c75272a"}]
      """
    And a stand-in "uvx"
    And the environment sets "XDG_CACHE_HOME" to "cache"
    And a file "cache/pubrules/providers/fe7bb78d115e16240d35e7990fc029399a1d2023d77541fb1cee658edac4b081/pubdeploy-9.0.0-py3-none-any.whl" holding "pubdeploy"
    And a file "cache/pubrules/providers/c71bff738d91630386010f388ac9252cc2c1926f0e3753242dec9c136c75272a/pubsec-9.0.0-py3-none-any.whl" holding "pubsec"
    When I call pubrules with "sync"
    Then it succeeds
    And the file "pubdeploy" can be run
    And the file "pubdeploy" includes "fetch https://github.com/example-org/pubdeploy/releases/download/v9.0.0/pubdeploy-9.0.0-py3-none-any.whl fe7bb78d115e16240d35e7990fc029399a1d2023d77541fb1cee658edac4b081 providers deploy.providers"
    And the file "pubdeploy" includes "provider https://github.com/example-org/pubsec/releases/download/v9.0.0/pubsec-9.0.0-py3-none-any.whl c71bff738d91630386010f388ac9252cc2c1926f0e3753242dec9c136c75272a"
    And the file "pubdeploy" does not include "provider https://github.com/example-org/pubdeploy/"
    And the file "pubdeploy" ends with "exec uvx --from "$fetched" ${with[@]+"${with[@]}"} pubdeploy "$@""
    When I run "bash pubdeploy --version"
    Then it succeeds
    And "uvx" was run with "--from cache/pubrules/providers/fe7bb78d115e16240d35e7990fc029399a1d2023d77541fb1cee658edac4b081/pubdeploy-9.0.0-py3-none-any.whl --with cache/pubrules/providers/c71bff738d91630386010f388ac9252cc2c1926f0e3753242dec9c136c75272a/pubsec-9.0.0-py3-none-any.whl pubdeploy --version"

  Scenario: Without a deploy block there is no pubdeploy
    Given a "service" repository
    When I call pubrules with "sync"
    Then it succeeds
    And the file "pubdeploy" does not exist

  Scenario: Sync lists the pubsec that pubsec already runs and keeps a newer one listed
    Given a "service" repository
    And pubrules has synced it
    And the release state lists "pubsec" at "9.9.9"
    And pubrules.json gets a deploy block
    When I call pubrules with "sync"
    Then it succeeds
    And the deploy.providers of pubrules.json is:
      """
      [{"release": "pubdeploy"}, {"release": "pubsec"}]
      """
    When deploy.providers changes to:
      """
      [{"release": "pubsec"},
       {"distribution": "pubdeploy", "sha256": "1111111111111111111111111111111111111111111111111111111111111111", "wheel": "https://github.com/example-org/pubdeploy/releases/download/v9.0.0/pubdeploy-9.0.0-py3-none-any.whl"}]
      """
    And I call pubrules with "sync"
    Then it succeeds
    And the deploy.providers of pubrules.json is:
      """
      [{"release": "pubsec"},
       {"distribution": "pubdeploy", "sha256": "1111111111111111111111111111111111111111111111111111111111111111", "wheel": "https://github.com/example-org/pubdeploy/releases/download/v9.0.0/pubdeploy-9.0.0-py3-none-any.whl"}]
      """
    And the file "pubdeploy" includes "fetch https://github.com/example-org/pubdeploy/releases/download/v9.0.0/pubdeploy-9.0.0-py3-none-any.whl 1111111111111111111111111111111111111111111111111111111111111111 providers deploy.providers"
    When I call pubrules with "sync --check"
    Then it succeeds

  Scenario: An entry without its sha256 fails check and sync writes the pin in its place
    Given a repository with a deploy block whose deploy.providers is:
      """
      [{"distribution": "pubsec", "wheel": "https://github.com/example-org/pubsec/releases/download/v0.0.1/pubsec-0.0.1-py3-none-any.whl"},
       {"release": "pubdeploy"}]
      """
    When I call pubrules with "sync --check"
    Then the exit code is 1
    And stderr mentions "deploy.providers lists pubsec without a release wheel and its sha256"
    When I call pubrules with "sync"
    Then it succeeds
    And the deploy.providers of pubrules.json is:
      """
      [{"release": "pubsec"}, {"release": "pubdeploy"}]
      """

  Scenario: The sync hooks run and their lines are printed
    Given a repository with a deploy block that lists both providers at their releases
    And pubdeploy's sync hook says "pubdeploy: deploy: drift, k8s.secrets-declared:example"
    When I call pubrules with "sync"
    Then it succeeds
    And stderr mentions "pubdeploy: deploy: drift, k8s.secrets-declared:example"
    When I call pubrules with "sync --check"
    Then the exit code is 1
    And stderr mentions "pubdeploy: deploy: drift"
    And pubdeploy's sync hook was asked of the namespace "example" to write, then to check

  Scenario: A sync hook that fails is said by its type alone and fails check
    Given a repository with a deploy block that lists both providers at their releases
    And the secret "canary-7f3e"
    And the sync hook "other" raises ValueError "the token is canary-7f3e"
    When I call pubrules with "sync"
    Then it succeeds
    When I call pubrules with "sync --check"
    Then the exit code is 1
    And stderr mentions "pubrules: the sustenaut.sync hook other failed (ValueError)"
    And no secret appears in what it produced

  Scenario: A deploy block without pubdeploy installed fails check
    Given a repository with a deploy block that lists both providers at their releases
    And pubdeploy's hooks are not installed
    When I call pubrules with "sync"
    Then it succeeds
    When I call pubrules with "sync --check"
    Then the exit code is 1
    And stderr mentions "pubrules: deploy: the deploy block is pubdeploy's to check, and pubdeploy is not installed"

  Scenario: A repository without a deploy block needs no pubdeploy
    Given a "utility" repository
    And pubdeploy's hooks are not installed
    When I call pubrules with "sync"
    Then it succeeds
    When I call pubrules with "sync --check"
    Then it succeeds

  Scenario: Bump runs the bump hooks before sync writes the launchers
    Given a repository with a deploy block that lists both providers at their releases
    And the bump hook "other" raises OSError "no network"
    And pubdeploy's bump hook moves its pin to "https://github.com/example-org/pubdeploy/releases/download/v9.1.0/pubdeploy-9.1.0-py3-none-any.whl" with the sha256 "2222222222222222222222222222222222222222222222222222222222222222"
    And locking the repository is recorded, not done
    When I bump the repository
    Then it succeeds
    And it prints "pubrules: the sustenaut.bump hook other failed (OSError)"
    And it prints "updated  pubrules.json (deploy.providers pubdeploy: 9.1.0)"
    And the file "pubdeploy" includes "fetch https://github.com/example-org/pubdeploy/releases/download/v9.1.0/pubdeploy-9.1.0-py3-none-any.whl 2222222222222222222222222222222222222222222222222222222222222222 providers deploy.providers"

  Scenario Outline: The removed commands are gone
    When I call pubrules with "<command>"
    Then the exit code is 2

    Examples:
      | command       |
      | deploy walk   |
      | k8s preflight |

  Scenario: The nightly runs the bump hooks and syncs what they moved
    The nightly moves the pins as `pubrules bump` does: a moved provider
    reaches the launchers and the lock.
    Given a repository with a deploy block that lists both providers at their releases
    And pubrules has synced it
    And the bump hook "other" raises OSError "no network"
    And pubdeploy's bump hook moves its pin to "https://github.com/example-org/pubdeploy/releases/download/v9.2.0/pubdeploy-9.2.0-py3-none-any.whl" with the sha256 "3333333333333333333333333333333333333333333333333333333333333333"
    And locking the repository is recorded, not done
    When the nightly moves the repository's pins
    Then the result includes "pubrules.json (deploy.providers pubdeploy: 9.2.0)"
    And stderr mentions "pubrules: the sustenaut.bump hook other failed (OSError)"
    And the repository was locked 1 times
    And the file "pubdeploy" includes "fetch https://github.com/example-org/pubdeploy/releases/download/v9.2.0/pubdeploy-9.2.0-py3-none-any.whl 3333333333333333333333333333333333333333333333333333333333333333 providers deploy.providers"

  Scenario: The nightly with nothing moved neither syncs nor locks
    Given a repository with a deploy block that lists both providers at their releases
    And pubrules has synced it
    And pubdeploy's bump hook moves nothing
    And locking the repository is recorded, not done
    And syncing the repository is recorded, not done
    When the nightly moves the repository's pins
    Then the result is "[]"
    And the repository was locked 0 times
    And the repository was synced 0 times
