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

  Scenario: A file in the Actions runner's temporary directory is allowed
    Given RUNNER_TEMP names the directory "runner-temp"
    When pubkit confines the file "runner-temp/changed-files"
    Then it is allowed

  Scenario: A row in a category pubrel does not know is refused
    When pubkit records "Tidied" as a row in the category "tidied"
    Then it stops with "pubrel has no category 'tidied'"

  Scenario: A tool's version is the first x.y.z it prints
    When pubkit reads the version of "python3"
    Then it is three numbers
