Feature: pubkit.json's security block configures Dependabot and SonarQube Cloud
  `pubkit sync` writes .github/dependabot.yml from security.dependabot, and
  every sonar job adds security.sonar.args and sends the code to
  security.sonar.organization. The advisory checks have no
  setting.

  Scenario: Without settings, Dependabot updates the GitHub Actions weekly
    Given the security settings '{"kind": "utility"}'
    Then dependabot.yml updates "github-actions" at "/" "weekly"

  Scenario: The ecosystems, schedule and options come from pubkit.json
    Given the security settings '{"kind": "utility", "security": {"dependabot": {"schedule": "daily", "ecosystems": ["npm", {"package-ecosystem": "pip", "directory": "/tests"}], "options": {"open-pull-requests-limit": 3}}}}'
    Then dependabot.yml updates "npm" at "/" "daily"
    And dependabot.yml updates "pip" at "/tests" "daily"
    And every dependabot.yml entry has "open-pull-requests-limit" 3

  Scenario: Dependabot set to false leaves dependabot.yml to the repository
    Given the security settings '{"kind": "utility", "security": {"dependabot": false}}'
    Then pubkit writes no dependabot.yml

  Scenario Outline: A security setting pubkit cannot use stops it with a message
    Given the security settings '<config>'
    Then reading them stops with "<message>"

    Examples:
      | config                                                          | message                                   |
      | {"security": {"dependabot": {"schedule": "hourly"}}}           | schedule must be one of daily             |
      | {"security": {"dependabot": {"labels": ["deps"]}}}             | other Dependabot settings go in options   |
      | {"security": {"dependabot": {"ecosystems": []}}}               | ecosystems must list ecosystems           |
      | {"security": {"dependabot": {"options": []}}}                  | options must be an object                 |
      | {"security": {"dependabot": "weekly"}}                         | security.dependabot must be an object     |
      | {"security": {"sonar": {"schedule": "daily"}}}                 | security.sonar has no setting schedule    |
      | {"security": {"sonar": {"args": 1}}}                           | security.sonar.args must be a string      |
      | {"security": {"sonar": "-Dx=y"}}                               | security.sonar must be an object          |
      | {"security": {"sonar": {"organization": "Example-Org"}}}         | security.sonar.organization must be a     |
      | {"security": {"sonar": {"organization": 7}}}                   | security.sonar.organization must be a     |

  Scenario: pubkit sync writes dependabot.yml and sync --check sees it drift
    Given a repository initialised as "utility"
    Then "pubkit.json" exists beside ".github/dependabot.yml" naming "github-actions"
    When the file ".github/dependabot.yml" reads "version: 2"
    And I run pubkit "sync" "--check"
    Then it fails

  Scenario Outline: Every sonar job adds the properties pubkit.json gives
    When I parse the workflow "<workflow>"
    Then its sonar job reads security.sonar.args from pubkit.json into the scan

    Examples:
      | workflow      |
      | rust-ci.yml   |
      | node-ci.yml   |
      | python-ci.yml |
