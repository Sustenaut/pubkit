Feature: pubkit service-account-key create makes a key and puts it in Bitwarden
  No file, command line or printed line ever holds the key. A refusal comes before a
  key exists, and nothing happens without "yes". The account's other keys are left.

  Scenario: A new key goes to a new secret
    Given a service account with another key
    When I make the key, giving the token "write-token" and answering "yes"
    Then it succeeds
    And gcloud made one key, on stdout
    And Bitwarden's SDK created "the-key" with the key, base64-encoded, in the project
    And the SDK logged in with the token "write-token"
    And no command line held the token or the key
    And neither the key nor the token is printed
    And no file under the repository holds the key
    And it prints "old-key-id"

  Scenario: --dry-run shows the plan and asks for nothing
    Given a service account with another key
    When I make the key with "--dry-run", giving nothing
    Then it succeeds
    And gcloud was not called
    And it prints "the-project"

  Scenario: A secret already there is refused, before a key exists
    Given a service account with another key
    And the project already has the secret "the-key"
    When I make the key, giving the token "write-token" and answering "yes"
    Then it fails
    And gcloud was not called
    And it prints "--replace"

  Scenario: --replace updates the secret that is there
    Given a service account with another key
    And the project already has the secret "the-key"
    When I make the key with "--replace", giving the token "write-token" and answering "yes"
    Then it succeeds
    And Bitwarden's SDK updated the existing "the-key" with the key

  Scenario: Anything but yes makes no key
    Given a service account with another key
    When I make the key, giving the token "write-token" and answering "no"
    Then it fails
    And gcloud was not called

  Scenario: No token makes no key
    Given a service account with another key
    When I make the key, giving no token
    Then it fails
    And gcloud was not called

  Scenario: A write Bitwarden refuses deletes the new key, and quotes neither secret
    Given a service account with another key
    And Bitwarden refuses writes, quoting what it was given
    When I make the key, giving the token "write-token" and answering "yes"
    Then it fails
    And gcloud deleted the new key
    And neither the key nor the token is printed

  Scenario: An address that is not a service account's is refused
    Given a service account with another key
    When I make the key for "someone@example.org", giving the token "write-token" and answering "yes"
    Then it fails
    And gcloud was not called

  Scenario: Without the SDK, it is installed for this run, by hash
    Given a service account with another key
    And the SDK is not installed
    When I make the key, giving the token "write-token" and answering "yes"
    Then it succeeds
    And uv installed the SDK with --require-hashes
    And Bitwarden's SDK created "the-key" with the key, base64-encoded, in the project

  Scenario: An SDK that won't install stops before a key exists
    Given a service account with another key
    And the SDK is not installed
    And uv refuses to install it
    When I make the key, giving the token "write-token" and answering "yes"
    Then it fails
    And gcloud was not called
