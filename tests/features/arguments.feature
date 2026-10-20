Feature: A command-line argument cannot reach outside the repository
  A file pubkit writes or reads because an argument named it lies in the
  working directory or a temporary directory, and an argument pubkit passes
  on to another program can never be taken for one of that program's
  options.

  Background:
    Given an empty repository

  Scenario Outline: A file outside the repository and the temporary directories is refused
    When I run pubkit <args>
    Then it fails
    And stderr mentions "must be in the working directory or a temporary directory"

    Examples:
      | args                                                                        |
      | "nightly" "--pubrel" "0.5.3" "--pub" "0.2.1" "--body" "/etc/pubkit-body.md" |
      | "crates" "--report" "../../../../../../../etc/pubkit-crates.json"           |

  Scenario: A row's text that pubrel would take for an option is refused
    When I run pubkit "record" "--" "--package=other"
    Then it fails
    And stderr mentions "which pubrel would take for an option"

  Scenario: A crate name that cargo would take for an option is refused
    When the supply chain of the crate "--manifest-path=/elsewhere/Cargo.toml" is resolved
    Then it stops with "is not a crate name"
