Feature: pubkit is also a Claude Code plugin marketplace
  `/plugin marketplace add Sustenaut/pubkit` reads .claude-plugin/marketplace.json
  at the repository root, and each plugin it lists is installed from its own
  directory. A malformed manifest, or an agent pointing at a file its plugin
  does not ship, fails only when someone installs it, so the layout is
  checked here.

  Scenario: Every listed plugin has a manifest and its own directory
    Then every plugin the marketplace lists has a plugin.json naming it

  Scenario: Each agent has the frontmatter Claude Code needs
    Then every plugin agent states a name and a description

  Scenario: Each file an agent reads from its plugin is shipped with it
    Then every ${CLAUDE_PLUGIN_ROOT} path an agent names exists in its plugin
