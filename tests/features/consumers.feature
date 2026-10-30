Feature: pubkit names no repository that consumes it
  What identifies a consumer lives on the consumer's side, in its pubkit.json.
  pubkit may name itself, the tools it depends on, and neutral examples, so
  src/, actions/, terraform/ and .github/ name no other owner/repo.

  Scenario: Nothing pubkit ships names a repository but its own and its tools'
    When I scan what pubkit ships for owner/repo names
    Then it finds none but those the guard allows

  Scenario: The allowances still have something to allow
    When I scan what pubkit ships for owner/repo names
    Then every allowance is still used

  Scenario Outline: The guard finds a repository, however it is named
    Given a shipped file reads "<text>"
    When I scan it for owner/repo names
    Then it finds "<name>"

    Examples:
      | text                                          | name             |
      | see https://github.com/some-org/some-repo.git | some-org/some-repo |
      | git@github.com:some-org/some-repo             | some-org/some-repo |
      | uses: some-org/some-repo/action@v1            | some-org/some-repo |
      | gh release view --repo some-org/some-repo     | some-org/some-repo |
      | latest some-org/some-repo                     | some-org/some-repo |
      | {"repo": "some-org/some-repo"}                | some-org/some-repo |
      | REPOS=some-org/some-repo                      | some-org/some-repo |
      | such as schryer/some-repo                     | schryer/some-repo  |
      | such as Sustenaut/some-repo                   | Sustenaut/some-repo |

  Scenario Outline: The guard lets pubkit, pubrel and neutral examples through
    Given a shipped file reads "<text>"
    When I scan it for owner/repo names
    Then it finds nothing

    Examples:
      | text                                                |
      | uses: Sustenaut/pubkit/actions/cache@v1             |
      | uses: anyone/pubrel/.github/workflows/release.yml@v1 |
      | https://github.com/example-org/some-cli             |
      | uses: actions/checkout@v5                           |
      | src/pubkit/refs.py and .github/workflows/ci.yml     |
