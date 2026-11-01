Feature: pubkit github-app create makes an app and puts its values in Bitwarden
  The manifest is the repository's, so pubkit names nothing of anyone's. A
  refusal comes before an app exists. The token is asked for, and reaches bws in
  its environment; the key is never printed; nothing is written without "yes".

  Scenario: An organization's app goes through the whole flow
    Given a manifest for the org "example-org"
    When I create the app, giving the token "write-token" and answering "yes"
    Then it succeeds
    And the browser posted to "https://github.com/organizations/example-org/settings/apps/new"
    And GitHub was sent the manifest with a redirect to 127.0.0.1 and no pubkit section
    And the browser opened "https://github.com/apps/the-app/installations/new"
    And GitHub was asked for the installations with a JWT signed by the new key
    And bws created "the-app-app-id" as "5551234"
    And bws created "the-app-installation-id" as "7770001"
    And bws created "the-app-app-key" as the key
    And bws wrote 3 secrets with the token "write-token"
    And neither the key nor the token is printed
    And the token is on no command line
    And no file under the repository holds the key

  Scenario: A user's app goes to the user's own settings
    Given a manifest for the user "someone"
    When I create the app, giving the token "write-token" and answering "yes"
    Then it succeeds
    And the browser posted to "https://github.com/settings/apps/new"
    And bws wrote 3 secrets with the token "write-token"

  Scenario: The installation that counts is the owner's, once it is there
    Given a manifest for the org "example-org"
    And the first look finds no installation, and the next one on another account too
    When I create the app, giving the token "write-token" and answering "yes"
    Then it succeeds
    And bws created "the-app-installation-id" as "7770001"

  Scenario: An installation on every repository says so, and where to narrow it
    Given a manifest for the org "example-org"
    When I create the app, giving the token "write-token" and answering "yes"
    Then it succeeds
    And it prints "It reaches ALL of example-org's repositories"
    And it prints "https://github.com/organizations/example-org/settings/installations/7770001"

  Scenario: A user's installation is narrowed in the user's own settings
    Given a manifest for the user "someone"
    When I create the app, giving the token "write-token" and answering "yes"
    Then it succeeds
    And it prints "https://github.com/settings/installations/7770001"

  Scenario: An installation on selected repositories lists them
    Given a manifest for the org "example-org"
    And the app is installed on the selected repositories "example-org/two" and "example-org/one"
    When I create the app, giving the token "write-token" and answering "yes"
    Then it succeeds
    And it prints "It reaches 2 selected repositories: example-org/one, example-org/two."
    And the list was asked for with the installation's own token
    And neither the key nor the token is printed

  Scenario: A long list of selected repositories is read to its end
    Given a manifest for the org "example-org"
    And the app is installed on 150 selected repositories
    When I create the app, giving the token "write-token" and answering "yes"
    Then it succeeds
    And it prints "It reaches 150 selected repositories:"
    And it prints "example-org/repo-149"

  Scenario: Failing to list the repositories says so, and still writes the key
    Given a manifest for the org "example-org"
    And the app is installed on the selected repositories "example-org/one" and "example-org/two"
    And GitHub refuses the installation token
    When I create the app, giving the token "write-token" and answering "yes"
    Then it succeeds
    And it prints "could not list them"
    And it prints "https://github.com/organizations/example-org/settings/installations/7770001"
    And bws wrote 3 secrets with the token "write-token"

  Scenario: An installation that does not say what it reaches is not taken for either
    Given a manifest for the org "example-org"
    And GitHub does not say which repositories the app reaches
    When I create the app, giving the token "write-token" and answering "yes"
    Then it succeeds
    And it prints "GitHub did not say which repositories it reaches"

  Scenario: A callback with the wrong state is refused, and the real one still works
    Given a manifest for the org "example-org"
    And the browser first comes back with the wrong state
    When I create the app, giving the token "write-token" and answering "yes"
    Then it succeeds
    And the wrong state was answered with 400
    And GitHub was given the code once

  Scenario: --dry-run shows the plan and does nothing
    Given a manifest for the org "example-org"
    When I create the app with "--dry-run", giving nothing
    Then it succeeds
    And it prints "the-app-app-key"
    And it prints "the-project"
    And it prints "--dry-run: no app was made and nothing was written."
    And no browser opened
    And bws was called 0 times

  Scenario: A name already in the project is refused before there is an app
    Given a manifest for the org "example-org"
    And Bitwarden already has "the-app-app-key"
    When I create the app, giving the token "write-token" and answering "yes"
    Then the exit code is 1
    And it prints "already in project the-project: the-app-app-key"
    And no browser opened
    And bws wrote 0 secrets with the token "write-token"

  Scenario: --replace edits what is there and creates what is not
    Given a manifest for the org "example-org"
    And Bitwarden already has "the-app-app-key"
    When I create the app with "--replace", giving the token "write-token" and answering "yes"
    Then it succeeds
    And bws edited the existing "the-app-app-key" to the key
    And bws created "the-app-app-id" as "5551234"
    And bws wrote 3 secrets with the token "write-token"
    And neither the key nor the token is printed

  Scenario: --replace will not guess between two secrets of one name
    Given a manifest for the org "example-org"
    And Bitwarden already has "the-app-app-key" twice
    When I create the app with "--replace", giving the token "write-token" and answering "yes"
    Then the exit code is 1
    And it prints "more than one secret"
    And no browser opened

  Scenario Outline: Anything but yes writes nothing, and says what is left to do
    Given a manifest for the org "example-org"
    When I create the app, giving the token "write-token" and answering "<answer>"
    Then the exit code is 1
    And it prints "Nothing was written"
    And it prints "https://github.com/organizations/example-org/settings/apps/the-app"
    And bws wrote 0 secrets with the token "write-token"
    And neither the key nor the token is printed

    Examples:
      | answer |
      | no     |
      | YES    |
      | y      |

  Scenario: No token means no app
    Given a manifest for the org "example-org"
    When I create the app, giving no token
    Then the exit code is 1
    And it prints "no access token"
    And no browser opened

  Scenario: A token that sees no such project means no app
    Given a manifest whose project is "another-project"
    When I create the app, giving the token "write-token" and answering "yes"
    Then the exit code is 1
    And it prints "It sees: the-project"
    And no browser opened

  Scenario: A failed write hides the key bws echoed, and the rest go with another token
    Given a manifest for the org "example-org"
    And bws refuses writes with the token "read-token", printing what it was given
    When I create the app, giving the tokens "read-token" and "write-token" and answering "yes"
    Then it succeeds
    And it prints "FAILED   the-app-app-key"
    And bws wrote 3 secrets with the token "write-token"
    And neither the key nor the token is printed

  Scenario: Giving up after a failed write says the key is gone
    Given a manifest for the org "example-org"
    And bws refuses writes with the token "read-token", printing what it was given
    When I create the app, giving the token "read-token" and answering "yes"
    Then the exit code is 1
    And it prints "Not written: the-app-app-id, the-app-app-key, the-app-installation-id"
    And it prints "make a new one at https://github.com/organizations/example-org/settings/apps/the-app"
    And neither the key nor the token is printed

  Scenario: GitHub refusing the code ends the run, with nothing written
    Given a manifest for the org "example-org"
    And GitHub refuses the code
    When I create the app, giving the token "write-token" and answering "yes"
    Then the exit code is 1
    And it prints "GitHub answered the manifest's code with 404"
    And bws wrote 0 secrets with the token "write-token"

  Scenario: A missing bws is said plainly
    Given a manifest for the org "example-org"
    And bws is not installed
    When I create the app, giving the token "write-token" and answering "yes"
    Then the exit code is 1
    And it prints "bws is not installed"
    And no browser opened

  Scenario Outline: A wrong manifest is refused
    Given the manifest reads <manifest>
    When I create the app, giving the token "write-token" and answering "yes"
    Then the exit code is 1
    And it prints "<says>"
    And no browser opened

    Examples:
      | manifest                                                                                                                | says                            |
      | not json                                                                                                                | is not JSON                     |
      | []                                                                                                                      | it must be an object            |
      | {"name": "a", "url": "b"}                                                                                               | a "pubkit" section              |
      | {"url": "b", "pubkit": {"org": "o", "bitwarden_project": "p", "secret_prefix": "s"}}                                    | it needs "name"                 |
      | {"name": "a", "url": "b", "redirect_url": "c", "pubkit": {"org": "o", "bitwarden_project": "p", "secret_prefix": "s"}}  | "redirect_url" is not its to set |
      | {"name": "a", "url": "b", "pubkit": {"bitwarden_project": "p", "secret_prefix": "s"}}                                   | one of "org" and "user"         |
      | {"name": "a", "url": "b", "pubkit": {"org": "o", "user": "u", "bitwarden_project": "p", "secret_prefix": "s"}}          | one of "org" and "user"         |
      | {"name": "a", "url": "b", "pubkit": {"org": "o/../x", "bitwarden_project": "p", "secret_prefix": "s"}}                  | is not an account's name        |
      | {"name": "a", "url": "b", "pubkit": {"org": "o", "secret_prefix": "s"}}                                                 | needs "bitwarden_project"       |
      | {"name": "a", "url": "b", "pubkit": {"org": "o", "bitwarden_project": "p", "secret_prefix": "Bad_Name"}}                | "secret_prefix" must be         |
      | {"name": "a", "url": "b", "pubkit": {"org": "o", "bitwarden_project": "p", "secret_prefix": "s", "extra": 1}}           | extra is not a pubkit setting   |

  Scenario: A manifest outside the repository is refused
    Given a manifest for the org "example-org"
    When I run pubkit "github-app" "create" "/etc/pubkit-manifest.json"
    Then it fails
    And stderr mentions "must be in the working directory or a temporary directory"
