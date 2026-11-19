Feature: pubrules' apt-install action installs apt packages from the Actions cache
  A slow Ubuntu mirror can take minutes to give a job its packages, so the
  apt-install action keeps the package lists and the .deb files in the
  Actions cache, under a key of the runner image, the packages and the run,
  restored by all but the run, so the newest entry wins. On a hit the
  packages are installed with nothing fetched and no apt-get update; a hit
  that will not install, or a miss, updates the lists and installs through
  the retry action's loop, and saves what it fetched as this run's entry. Each
  scenario runs the action's steps as GitHub does, with their inputs in the
  environment and stand-ins for sudo, apt-get, dpkg, lsb_release and sleep.

  Scenario: The key names the runner image, the architecture, a digest of the packages and the run
    When apt-install plans "qemu-user gcc-s390x-linux-gnu" on the runner image "ubuntu24" "20261110.1"
    Then the plan passes
    And the restore prefix is "apt-ubuntu24-20261110.1-amd64-", the sha256 of "gcc-s390x-linux-gnu\nqemu-user\nno-install-recommends=true\n" and "-"
    And the key is the restore prefix and "7-2", the run and its attempt
    And the cache directory is "apt-install" in the runner's temporary directory

  Scenario: The same packages in another order, or named twice, have the same key
    When apt-install plans "qemu-user gcc-s390x-linux-gnu" and "gcc-s390x-linux-gnu qemu-user  qemu-user" on the runner image "ubuntu24" "20261110.1"
    Then the two keys are the same

  Scenario: Packages on lines of their own, as a YAML block gives them, have the key of the same packages on one line
    When apt-install plans "qemu-user\ngcc-s390x-linux-gnu\n" and "gcc-s390x-linux-gnu qemu-user" on the runner image "ubuntu24" "20261110.1"
    Then the two keys are the same

  Scenario Outline: Another package list or flag has another key
    When apt-install plans "<first>" and "<second>" on the runner image "ubuntu24" "20261110.1", the second with no-install-recommends "<flag>"
    Then the two keys differ

    Examples:
      | first     | second        | flag  |
      | qemu-user | qemu-user gcc | true  |
      | qemu-user | qemu-user     | false |

  Scenario: A new runner image has a new key
    When apt-install plans "qemu-user" on the runner image "ubuntu24" "20261110.1"
    And apt-install plans "qemu-user" on the runner image "ubuntu24" "20261117.2"
    Then the two keys differ

  Scenario: Off a hosted runner, the key names the release's codename and "self-hosted"
    When apt-install plans "qemu-user" on a runner with no image, of the release "noble"
    Then the plan passes
    And the restore prefix starts "apt-noble-self-hosted-amd64-"

  Scenario Outline: A package or flag apt-install does not take stops the step before any install
    When apt-install plans "<packages>" with no-install-recommends "<flag>"
    Then the plan failed with 2
    And it reported the error "<error>"

    Examples:
      | packages          | flag  | error                                      |
      |                   | true  | no packages to install                     |
      | -o=APT::Foo       | true  | '-o=APT::Foo' is not a package name        |
      | jq;touch          | true  | 'jq;touch' is not a package name           |
      | jq\n--bad curl    | true  | '--bad' is not a package name              |
      | jq                | yes   | no-install-recommends is true or false     |

  Scenario: On a hit the packages are installed from the cache, with nothing fetched and nothing saved
    When apt-install installs "qemu-user gcc-s390x-linux-gnu" on a hit
    Then the step passed
    And apt-get ran "install -y --no-install-recommends --no-download qemu-user gcc-s390x-linux-gnu" alone
    And the step asked for no save

  Scenario: Packages on lines of their own are installed as on one line
    When apt-install installs "qemu-user\ngcc-s390x-linux-gnu\n" on a hit
    Then apt-get ran "install -y --no-install-recommends --no-download qemu-user gcc-s390x-linux-gnu" alone

  Scenario: A hit that will not install from the cache updates the lists, installs, and saves this run's entry
    When apt-install installs "qemu-user" on a hit whose packages the cache lacks
    Then the step passed
    And apt-get ran "install -y --no-install-recommends --no-download qemu-user", "update" and "install -y --no-install-recommends qemu-user"
    And it warned "apt-install: the cached lists or packages did not install; updating them"
    And the step asked for a save

  Scenario: On a miss the lists are updated, the packages installed and the cache saved
    When apt-install installs "qemu-user" on a miss
    Then the step passed
    And apt-get ran "update" and "install -y --no-install-recommends qemu-user"
    And the step asked for a save
    And the cache directory holds the lists and packages, owned by the runner, with no lock or partial download

  Scenario: A restore the cache service refused is a miss: the packages are installed from the mirror
    When apt-install installs "qemu-user" after a restore that failed
    Then the step passed
    And apt-get ran "update" and "install -y --no-install-recommends qemu-user"

  Scenario: An install that fails once is tried again
    When apt-install installs "qemu-user" on a miss, with a mirror that fails the first time
    Then the step passed
    And apt-get ran "update", "install -y --no-install-recommends qemu-user", "update" and "install -y --no-install-recommends qemu-user"
    And it warned "apt-get install failed (attempt 1 of 3)"
    And it slept 1 time for 15 seconds

  Scenario: A mirror that always fails fails the step after three tries
    When apt-install installs "qemu-user" on a miss, with a mirror that always fails
    Then the step failed with 1
    And it warned "apt-get install failed (attempt 3 of 3)"
    And the step asked for no save

  Scenario: no-install-recommends "false" installs the recommended packages too
    When apt-install installs "qemu-user" on a miss, with no-install-recommends "false"
    Then apt-get ran "update" and "install -y qemu-user"

  Scenario: Every apt-get keeps its lists and packages in the cache directory, and writes no package cache
    When apt-install installs "qemu-user" on a miss
    Then every apt-get was given a configuration that keeps the lists and packages in the cache directory

  Scenario: The cache steps never fail the job, restore the newest entry, and save only what was fetched
    When I read the apt-install action
    Then its restore and save use actions/cache at the commit actions/cache uses, and continue on error
    And its restore takes the newest entry by the restore prefix, and its save keeps this run's key
    And its save runs only when the install step asks for one
    And every run step of the "apt-install" action takes its inputs from its environment alone
