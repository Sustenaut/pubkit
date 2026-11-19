Feature: Keeping a repository's shared files in step
  `pubrules init` writes the shared files; `pubrules sync --check` fails when a
  managed one differs from the installed pubrules', so copies cannot drift.

  Scenario: init writes managed and seeded files for a Rust package
    Given an empty repository
    When I run pubrules "init" "--kind=rust" "--bin=demo"
    Then it succeeds
    And the repository has "AGENTS.md", "pubrules.mk", "rust-toolchain.toml", "tests/requirements-pubrules.txt", "tests/requirements.txt", "tests/pytest.ini", "Makefile", ".github/workflows/pr.yml", ".github/workflows/main.yml", ".github/workflows/nightly.yml"
    And the file "Makefile" includes "DEMO_BIN_DIR"
    And "tests/requirements-pubrules.txt" pins this pubrules' wheel

  Scenario: every repository gets the managed AGENTS.md, with the draft and up-to-date rules
    Given an empty repository
    When I run pubrules "init" "--kind=utility"
    Then it succeeds
    And it prints "managed  AGENTS.md"
    And the file "AGENTS.md" includes "Mark it **draft** only when something else must happen first"
    And the file "AGENTS.md" includes "Before each push, `git fetch origin main`"
    And the file "AGENTS.md" includes "Managed by pubrules"

  Scenario: An edited AGENTS.md fails the check, and sync restores it
    Given a repository initialised as "rust"
    And a file "AGENTS.md" holding "# Our own rules"
    When I run pubrules "sync" "--check"
    Then it fails
    And stderr mentions "AGENTS.md differs"
    When I run pubrules "sync"
    Then it succeeds
    And it prints "updated  AGENTS.md"

  Scenario: init keeps the repository's own files
    Given an empty repository
    And a file "tests/pytest.ini" holding "[pytest]"
    When I run pubrules "init" "--kind=utility"
    Then it succeeds
    And it prints "kept     tests/pytest.ini"
    And the file "tests/pytest.ini" includes "[pytest]"

  Scenario: init gives a Node package the Node CI, generated from pubrules.json
    Given an empty repository
    And a file "package.json" holding "{}"
    When I run pubrules "init" "--kind=utility"
    Then it succeeds
    And the file "pubrules.json" includes "ci"
    And the file ".github/workflows/pr.yml" includes "job-node-test.yml"
    And the file ".github/workflows/main.yml" includes "secrets: { SONAR_TOKEN: '${{ secrets.SONAR_TOKEN }}' }"

  Scenario: init gives a package with a pyproject.toml the Python CI, even beside a package.json
    Given an empty repository
    And a file "package.json" holding "{}"
    And a file "pyproject.toml" holding "[project]"
    When I run pubrules "init" "--kind=utility"
    Then it succeeds
    And the file ".github/workflows/pr.yml" includes "job-python-functional.yml"

  Scenario: A freshly initialised repository is in step
    Given a repository initialised as "utility"
    When I run pubrules "sync" "--check"
    Then it succeeds
    And it prints "in step with pubrules"

  Scenario: An edited managed file fails the check, and sync restores it
    Given a repository initialised as "rust"
    And a file "pubrules.mk" holding "edited"
    When I run pubrules "sync" "--check"
    Then it fails
    And stderr mentions "pubrules.mk differs"
    When I run pubrules "sync"
    Then it succeeds
    And it prints "updated  pubrules.mk"
    When I run pubrules "sync" "--check"
    Then it succeeds

  Scenario: init writes a .gitignore that ignores .env, and .venv as a directory or a link
    Given an empty repository
    When I run pubrules "init" "--kind=rust"
    Then it succeeds
    And ".gitignore" holds the line ".env" once
    And ".gitignore" ignores .venv as a directory and as a link

  Scenario: A .gitignore that doesn't ignore .env fails the check, and sync adds it to the rest
    Given a repository initialised as "utility"
    And a file ".gitignore" holding "/build/"
    When I run pubrules "sync" "--check"
    Then it fails
    And stderr mentions ".gitignore doesn't ignore .env"
    When I run pubrules "sync"
    Then it succeeds
    And it prints "updated  .gitignore (now ignores .env, .venv)"
    And ".gitignore" holds the line "/build/" once
    And ".gitignore" holds the line ".env" once
    And ".gitignore" holds the line ".venv" once
    When I run pubrules "sync" "--check"
    Then it succeeds
    When I run pubrules "sync"
    Then it prints "already in step"
    And ".gitignore" holds the line ".env" once
    And ".gitignore" holds the line ".venv" once

  Scenario: sync adds .env on a line of its own after a last line with no newline
    Given a repository initialised as "utility"
    And the .gitignore ends without a newline after "/build/"
    When I run pubrules "sync"
    Then it succeeds
    And ".gitignore" holds the line "/build/" once
    And ".gitignore" holds the line ".env" once

  Scenario: sync writes a .gitignore where there is none
    Given a repository initialised as "utility"
    And there is no ".gitignore"
    When I run pubrules "sync"
    Then it succeeds
    And ".gitignore" holds the line ".env" once

  Scenario: A .venv/ that matches only the directory fails the check, and sync drops its slash where it stands
    Given a repository initialised as "utility"
    And the .gitignore reads "/.env" and "/.venv/" and "/build/"
    When I run pubrules "sync" "--check"
    Then it fails
    And stderr mentions ".gitignore doesn't ignore .venv. The local environment, a directory or a link"
    When I run pubrules "sync"
    Then it succeeds
    And it prints "updated  .gitignore (now ignores .venv)"
    And ".gitignore" reads "/.env" and "/.venv" and "/build/"
    And ".gitignore" ignores .venv as a directory and as a link
    When I run pubrules "sync" "--check"
    Then it succeeds

  Scenario Outline: Every line that ignores .venv only as a directory loses its slash, and nothing is appended
    Given a repository initialised as "utility"
    And the .gitignore reads <lines>
    When I run pubrules "sync"
    Then it succeeds
    And ".gitignore" reads <mended>

    Examples:
      | lines                           | mended                        |
      | ".env" and ".venv/"             | ".env" and ".venv"            |
      | ".env" and "**/.venv/"          | ".env" and "**/.venv"         |
      | ".env" and ".venv/" and "/.venv/" | ".env" and ".venv" and "/.venv" |

  Scenario: A .venv that a later ! takes back is appended again, under its comment
    Given a repository initialised as "utility"
    And the .gitignore reads ".env" and ".venv/" and "!.venv"
    When I run pubrules "sync"
    Then it succeeds
    And ".gitignore" ignores .venv as a directory and as a link

  Scenario Outline: A pattern that covers .env counts, and a later ! takes it back
    Given a repository initialised as "utility"
    And the .gitignore reads <lines> and ".venv"
    When I run pubrules "sync" "--check"
    Then it <result>

    Examples:
      | lines                 | result   |
      | "/.env"               | succeeds |
      | ".env*"               | succeeds |
      | "**/.env"             | succeeds |
      | ".env/"               | fails    |
      | ".env" and "!.env"    | fails    |
      | "!.env" and ".env"    | succeeds |
      | ".envrc"              | fails    |

  Scenario: Seeded files are the repository's to change
    Given a repository initialised as "rust"
    And a file "Makefile" holding "include pubrules.mk"
    When I run pubrules "sync" "--check"
    Then it succeeds

  Scenario: A lock pinning another pubrules fails the check
    Given a repository initialised as "utility"
    And a file "tests/requirements.lock" holding "pubrules @ https://example.org/download/v0.0.0/pubrules-0.0.0-py3-none-any.whl"
    And PUBRULES_ANY_VERSION is set
    When I run pubrules "sync" "--check"
    Then it fails
    And stderr mentions "does not pin pubrules"

  Scenario: pubrules does not judge a repository that pins another release
    A stale .venv ran pubrules 0.9.x in repositories pinning 0.14.6, and
    those runs were taken for the pinned pubrules'.
    Given a repository initialised as "utility"
    And a file "tests/requirements.lock" holding "pubrules @ https://example.org/download/v0.0.0/pubrules-0.0.0-py3-none-any.whl"
    When I run pubrules "sync" "--check"
    Then the exit code is 2
    And stderr mentions "this repository pins pubrules 0.0.0, and this is pubrules"
    And stderr mentions "run `make venv`"
    And stderr mentions "`pubrules bump` moves the repository to this one"
    When I run pubrules "audit" "--check"
    Then the exit code is 2
    When I run pubrules "pins" "--no-lock"
    Then it succeeds

  Scenario: A moved pin rebuilds .venv before a target runs pubrules from it
    Given a repository initialised as "rust"
    And the lock pins this pubrules
    When I run make "--dry-run" "sync-check"
    Then it prints "python3 -m venv .venv"
    And it prints "cp tests/requirements.lock .venv/requirements.lock"
    And it prints ".venv/bin/pubrules sync --check"
    Given .venv was built from the current lock
    When I run make "--dry-run" "sync-check"
    Then it prints ".venv/bin/pubrules sync --check"
    And stdout does not mention "python3 -m venv"
    Given the lock moves to another pubrules
    When I run make "--dry-run" "sync-check"
    Then it prints "python3 -m venv .venv"

  Scenario: An older pubrules than the pin is not offered bump
    Given a repository initialised as "utility"
    And a file "tests/requirements.lock" holding "pubrules @ https://example.org/download/v999.0.0/pubrules-999.0.0-py3-none-any.whl"
    When I run pubrules "conform"
    Then the exit code is 2
    And stderr mentions "this repository pins pubrules 999.0.0"
    And stderr does not mention "pubrules bump"

  Scenario: sync outside an initialised repository says what to do
    Given an empty repository
    When I run pubrules "sync" "--check"
    Then it fails
    And stderr mentions "run `pubrules init"

  Scenario: lock pins every file the index publishes, so it installs on any platform
    Given an empty repository
    And a package "demo" 1.0 whose index lists files hashed "aaaa" and "bbbb"
    And a file "tests/requirements.txt" holding "demo==1.0"
    When I run pubrules "lock"
    Then it succeeds
    And "tests/requirements.lock" pins "demo==1.0" with the hashes "aaaa", "bbbb" and the resolved file's

  Scenario: lock refuses a pubtest an index gives, which is another project
    Given an empty repository
    And a package "pubtest" 1.0 whose index lists files hashed "aaaa" and "bbbb"
    And a file "tests/requirements.txt" holding "pubtest>=0.2.2"
    When I run pubrules "lock"
    Then it fails
    And stderr mentions "tests/requirements.txt must pin pubtest by its release wheel"
    And the file "tests/requirements.lock" does not exist

  Scenario: lock works where pip is not installed, as under uvx and ./pubrules
    Given an empty repository
    And a package "demo" 1.0 whose index lists files hashed "aaaa" and "bbbb"
    And a file "tests/requirements.txt" holding "demo==1.0"
    And pip can't be imported
    When I run pubrules "lock"
    Then it succeeds
    And "tests/requirements.lock" pins "demo==1.0" with the hashes "aaaa", "bbbb" and the resolved file's

  Scenario: lock keeps the #sha256 of a URL pin only a package requires, so the lock installs with --require-hashes
    Given an empty repository
    And a wheel "outer" 1.0 that requires the wheel "inner" 1.0 by its URL and sha256
    And tests/requirements.txt names "outer" by its URL
    When I run pubrules "lock"
    Then it succeeds
    And "tests/requirements.lock" pins "inner" by its URL with its #sha256, and "outer" by its URL as named
    And pip installs the lock with --require-hashes

  Scenario: The built wheel carries every template, and the standard's rules
    When pubrules' wheel is built
    Then it holds every file under src/pubrules/templates, and standard.json

  Scenario: supply-chain writes each crate's report beside its manifest
    Given a crate "demo" with no dependencies, vetted with audits imported from "mozilla" and "zcash"
    When I run pubrules "supply-chain" "demo"
    Then it succeeds
    And it prints "wrote SUPPLY-CHAIN.md"
    And the file "SUPPLY-CHAIN.md" includes "Generated by `pubrules supply-chain` from `supply-chain/`; do not edit."
    And the file "SUPPLY-CHAIN.md" includes "0 dependencies"
    And the file "SUPPLY-CHAIN.md" includes "Audits are imported from Mozilla and Zcash."

  Scenario: supply-chain --check passes on a current report and fails on a stale one
    Given a crate "demo" with no dependencies, vetted with audits imported from "mozilla" and "zcash"
    And pubrules "supply-chain" "demo" has run
    When I run pubrules "supply-chain" "--check" "demo"
    Then it succeeds
    And it prints "SUPPLY-CHAIN.md current for demo"
    When the file "SUPPLY-CHAIN.md" changes to:
      """
      edited
      """
    And I run pubrules "supply-chain" "--check" "demo"
    Then it fails
    And stderr mentions "stale; run `make supply-chain`: SUPPLY-CHAIN.md"

  Scenario: supply-chain names a crate that is not here
    Given a crate "demo" with no dependencies, vetted with audits imported from "mozilla" and "zcash"
    When I run pubrules "supply-chain" "other"
    Then it fails
    And stderr mentions "no crate here named other"

  Scenario: supply-chain needs cargo vet set up
    Given an empty repository
    When I run pubrules "supply-chain" "demo"
    Then it fails
    And stderr mentions "no supply-chain/config.toml here; run `cargo vet init` first"
