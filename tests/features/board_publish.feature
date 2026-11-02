Feature: A board is kept in storage only when something changed
  `pubkit board-publish` compares a new board with the one the head names, ignoring when
  it was read, and uploads it and moves the head only if a fact or a grade differs.
  These run it against a fake storage service.

  Background:
    Given an empty repository

  Scenario: The first board is uploaded, and the head's first move says so
    Given storage has no board for "some-tenant"
    When I publish a board to "some-tenant"
    Then it succeeds
    And it uploaded 1 file to "some-tenant"
    And the head moved with "If-None-Match: *"
    And it signed in with the job's token for storage's host name

  Scenario: A board that only differs in when it was read uploads nothing
    Given storage's board for "some-tenant" has the grade "A"
    When I publish a board to "some-tenant" with the grade "A", read later
    Then it succeeds
    And it prints "nothing changed"
    And it uploaded 0 files to "some-tenant"
    And the head did not move

  Scenario: A changed grade is uploaded, and the move names the old hash
    Given storage's board for "some-tenant" has the grade "A"
    When I publish a board to "some-tenant" with the grade "B", read later
    Then it succeeds
    And it uploaded 1 file to "some-tenant"
    And the head moved with If-Match on the old hash

  Scenario: Another move first fails the run, and overwrites nothing
    Given storage's board for "some-tenant" has the grade "A"
    And another writer moves the head first
    When I publish a board to "some-tenant" with the grade "B", read later
    Then it fails
    And stderr mentions "storage said 412"

  Scenario: Storage refusing the read stops it before any upload
    Given storage refuses to read the head with 403
    When I publish a board to "some-tenant"
    Then it fails
    And stderr mentions "storage said 403"
    And it uploaded 0 files to "some-tenant"

  Scenario: A tenant that isn't a name is refused before anything is sent
    When I publish a board to "../other"
    Then it fails
    And stderr mentions "--tenant must be"
    And it uploaded 0 files to "../other"

  Scenario: Without a job's token it says what the job needs
    When I publish a board to "some-tenant" outside a job
    Then it fails
    And stderr mentions "id-token: write"
    And it uploaded 0 files to "some-tenant"

  Scenario: A missing board is said, not thrown
    When I publish a missing board to "some-tenant"
    Then it fails
    And stderr mentions "board.json"

  Scenario: Nothing printed names a repository or the token
    Given storage has no board for "some-tenant"
    When I publish a board to "some-tenant"
    Then its output names no repository and no token

  Scenario Outline: A state file becomes the repos file for a role
    Given a state file for "example-org"
    When I turn it into the repos file for "<role>"
    Then it lists "<repos>"

    Examples:
      | role   | repos                                |
      | board  | example-org/one, example-org/two     |
      | govern | example-org/two, example-org/three   |

  Scenario: The repos file keeps each repository's roles and project
    Given a state file for "example-org"
    When I turn it into the repos file for "board"
    Then "example-org/one" has the project "tools", protect on and govern off
    And "example-org/two" has no project

  Scenario: A state file that is wrong is refused, saying why
    Given the file "state.json" reads "{"owner": "x"}"
    When I run pubkit "state" "--repos-for" "board"
    Then it fails
    And stderr mentions ""repos" must be an object"

  Scenario: state takes a check or a role, not both
    When I run pubkit "state" "--check" "--repos-for" "board"
    Then it fails
