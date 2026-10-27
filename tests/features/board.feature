Feature: The board grades every listed repository A to E
  `pubkit board` reads the repositories protected.json and governed.json
  list, reads each through one connector per source, and grades them by
  src/pubkit/board/rules.json: Quality and Security start at SonarQube
  Cloud's letters and Delivery at A, each taking a letter off per notch,
  never below E. The rules are tested on plain fact files, each connector
  against recorded responses, and the command on a recording of a whole
  build.

  Scenario: A repository with nothing wrong grades A
    Given the facts of a package with nothing wrong
    When I grade them
    Then Quality is "A", Security is "A", Delivery is "A" and overall "A"
    And no grade lists a notch

  Scenario: Quality starts at Sonar's worse rating of reliability and maintainability
    Given the facts of a package with nothing wrong
    And Sonar rates reliability "B", maintainability "C" and security "B"
    When I grade them
    Then Quality is "C", Security is "B", Delivery is "A" and overall "C"
    And Quality starts at "C" from "SonarQube Cloud: reliability B, maintainability C"

  Scenario: No Sonar analysis starts at A and takes a notch, for Quality and Security
    Given the facts of a package with nothing wrong
    And SonarQube Cloud has no analysis of it
    When I grade them
    Then Quality is "B", Security is "B", Delivery is "A" and overall "B"
    And Quality lists the notch "No SonarQube Cloud analysis"

  Scenario: Each other tool with findings takes a letter from its category
    Given the facts of a package with nothing wrong
    And main's last CI run has the failed jobs "ci / gates, ci / test (windows-latest), ci / audit"
    And it has 2 open Dependabot alerts
    When I grade them
    Then Quality is "C", Security is "C", Delivery is "B" and overall "C"
    And Quality lists the notch "format and lint has findings on main: ci / gates failed"
    And Security lists the notch "2 open Dependabot alerts"
    And Delivery lists the notch "Main's last CI run (ci.yml) is failure"

  Scenario: A public repository scoring under 7 on Scorecard takes a security notch
    Given the facts of a package with nothing wrong
    And OpenSSF Scorecard scores it 5.4
    When I grade them
    Then Security is "B"
    And Security lists the notch "OpenSSF Scorecard 5.4, below 7; Branch-Protection"

  Scenario: Alerts that are switched off are listed with no notch
    Given the facts of a package with nothing wrong
    And its Dependabot alerts could not be read, "Dependabot alerts are disabled for this repository. (HTTP 403)"
    When I grade them
    Then Security is "A"
    And Security lists the note "Dependabot alerts not read: Dependabot alerts are disabled for this repository. (HTTP 403)"

  Scenario: A package behind on pubkit, ungoverned and with open issues grades D
    Given the facts of a package with nothing wrong
    And it pins pubkit "0.12.0" where the latest is "0.14.2"
    And it is not in governed.json and has no nightly workflow
    And it has 3 open issues
    When I grade them
    Then Delivery is "D"
    And Delivery lists, in order, "pubkit 0.12.0, latest 0.14.2", "3 open issues", "Not in governed.json, and no nightly run in 2 days"

  Scenario: A package not integrated with pubrel, unreleased for two months
    Given the facts of a package with nothing wrong
    And it has no release.json, so is not integrated with pubrel
    And its last release was "v0.3.1" on "2026-08-01T10:00:00Z"
    When I grade them
    Then Delivery is "C"
    And Delivery lists the notch "Not integrated with pubrel"
    And Delivery lists the notch "Last release v0.3.1 on 2026-08-01, over 61 days ago"

  Scenario: A service is graded on observability and live testing, not on pubrel
    Given the facts of a service with nothing wrong
    When I grade them
    Then Quality is "A", Security is "A", Delivery is "A" and overall "A"

  Scenario: A service without a health URL or a recent green live test takes two notches
    Given the facts of a service with nothing wrong
    And its live block declares no health URL
    And its live test last passed at "2026-10-25T09:00:00Z"
    When I grade them
    Then Delivery is "C"
    And Delivery lists the notch "No health URL in pubkit.json's live block"
    And Delivery lists the notch "live.yml last passed 2026-10-25 09:00, over 24 h ago"

  Scenario: A service whose health URL does not answer takes the observability notch
    Given the facts of a service with nothing wrong
    And its health URL answered 503
    When I grade them
    Then Delivery is "B"
    And Delivery lists the notch "https://example.eu/health answered 503"

  Scenario: Protection that differs from pubkit's takes a notch, and a private repository's plan limit says so
    Given the facts of a service with nothing wrong
    And it is private and GitHub's plan gives it no rulesets
    When I grade them
    Then Delivery is "B"
    And Delivery lists the notch "No ruleset: Upgrade to GitHub Pro or make this repository public to enable this feature. (HTTP 403) (a GitHub plan limit)"

  Scenario: A grade never goes below E, but lists every notch
    Given the facts of a package with nothing wrong
    And it pins pubkit "0.12.0" where the latest is "0.14.2"
    And it is not in governed.json and has no nightly workflow
    And it has 3 open issues
    And it has no release.json, so is not integrated with pubrel
    And its last release was "v0.3.1" on "2026-08-01T10:00:00Z"
    And main's last CI run has the failed jobs "ci / osv"
    And its protection differs: "does not require ci / passed (app 15368)"
    When I grade them
    Then Delivery is "E"
    And Delivery takes 7 notches
    And the overall grade is "E"

  Scenario: Other rules can drop a notch
    Given the facts of a package with nothing wrong
    And it has 3 open issues
    And rules without the "issues" notch
    When I grade them
    Then Delivery is "A"

  Scenario: Rules naming a notch pubkit does not have are refused
    Given rules with a delivery notch "coffee"
    Then loading them fails with "no delivery notch coffee"

  Scenario: Rows sort worst first
    Given graded rows "a/one" "B" with 1 notch, "a/two" "E" with 6 notches, "a/three" "B" with 3 notches
    When I order them
    Then the order is "a/two, a/three, a/one"

  Scenario: The lists connector reads protected.json and governed.json
    Given recorded responses from the build recording
    When the lists connector reads "protected.json" and "governed.json"
    Then it lists "schryer/pubthing, schryer/visit-thing, schryer/private-thing"
    And "schryer/pubthing" is governed
    And every call is recorded with its connector, method, endpoint and time

  Scenario: The github connector reads a repository's files, issues and alerts
    Given recorded responses from the build recording
    When the github connector reads "schryer/pubthing"
    Then its visibility is "public"
    And its pubkit.json has the kind "rust"
    And it has 1 open issue, the pull request it lists not counted
    And its Dependabot alerts could not be read: "Dependabot alerts are disabled for this repository. (HTTP 403)"
    And its latest release is "v1.4.0"
    And every call is recorded with its connector, method, endpoint and time

  Scenario: The actions connector reads main's runs and their jobs
    Given recorded responses from the build recording
    When the actions connector reads main's runs of "schryer/pubthing"
    Then the newest run is "ci.yml", "success"
    And its jobs include "ci / gates" "success"

  Scenario: The sonarcloud connector reads ratings as letters, and no project as none
    Given recorded responses from the build recording
    When the sonarcloud connector reads "schryer/pubthing" and "schryer/visit-thing"
    Then "schryer/pubthing" rates reliability "A", maintainability "B" and security "A"
    And "schryer/visit-thing" has no ratings, noted "no SonarQube Cloud project schryer_visit-thing"

  Scenario: The scorecard connector reads a score, and an unscored repository as none
    Given recorded responses from the build recording
    When the scorecard connector reads "schryer/pubthing" and "schryer/visit-thing"
    Then "schryer/pubthing" scores 6.1
    And "schryer/visit-thing" is not scored

  Scenario: The live connector says whether a health URL answers
    Given recorded responses from the build recording
    When the live connector reads "https://visit.example.eu/health" and "https://down.example.eu/health"
    Then the first answers 200 and the second does not answer

  Scenario: pubkit board writes board.json and index.html from a recorded build
    Given the build recording, protected.json and governed.json in the repository
    When I run pubkit "board" "--out" "site" "--replay" "recorded.json"
    Then it succeeds
    And it prints "3 repos:"
    And "site/board.json" lists the repositories worst first
    And every fact and notch in "site/board.json" names calls that board.json records
    And "site/index.html" shows "schryer/private-thing" as private, with its grades
    And "site/index.html" has a breakdown naming "github · open_issues"
    And "site/index.html" loads no script

  Scenario: What --record keeps, --replay builds the same board from
    Given recorded responses from the build recording
    When I build the board while recording it, then build it again from that recording
    Then the two boards grade every repository the same, from the same calls

  Scenario: Only the connectors can reach the network
    When I read the board's modules outside its connectors
    Then none imports urllib, http, socket, ssl or subprocess
