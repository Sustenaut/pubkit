Feature: pubkit lists what it ships for other repositories to pin
  The installed wheel carries no actions, workflows or Terraform modules, so
  refs.SHIPPED names them for the guard that refuses a pin of something this
  pubkit lacks. The list is the repository's tree, exactly.

  Scenario: SHIPPED names every action, reusable workflow and Terraform module in the tree
    When I compare refs.SHIPPED with the repository's tree
    Then they name the same actions, reusable workflows and modules
