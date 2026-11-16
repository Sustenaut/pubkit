Feature: What sync and bump do for a deploy, now that the walk is pubdeploy's
  The managed with-secrets in every repository with a with-secrets.json, the
  launchers' provider wheels, pubsec's and pubdeploy's floors in
  deploy.providers, ./pubdeploy, and the sustenaut.sync and sustenaut.bump
  hooks, in a scratch repository with the hooks faked.

  Scenario: A utility with a with secrets json gets the managed with secrets
    Then a utility with a with secrets json gets the managed with secrets

  Scenario: A utility without one gets none
    Then a utility without one gets none

  Scenario: The launcher runs with each provider checked against its sha256
    Then the launcher runs with each provider checked against its sha256

  Scenario: The launcher without providers is as it was
    Then the launcher without providers is as it was

  Scenario: Sync lists pubsec and pubdeploy in deploy providers and check fails without them
    Then sync lists pubsec and pubdeploy in deploy providers and check fails without them

  Scenario: Sync writes pubsec and with secrets runs it
    Then sync writes pubsec and with secrets runs it

  Scenario: Sync writes pubdeploy which runs with the other providers
    Then sync writes pubdeploy which runs with the other providers

  Scenario: Without a deploy block there is no pubdeploy
    Then without a deploy block there is no pubdeploy

  Scenario: A floored package below the floor is moved up and a newer one kept
    Then a floored package below the floor is moved up and a newer one kept

  Scenario: The sync hooks run and their lines are printed
    Then the sync hooks run and their lines are printed

  Scenario: A sync hook that fails is said by its type alone and fails check
    Then a sync hook that fails is said by its type alone and fails check

  Scenario: A deploy block without pubdeploy installed fails check
    Then a deploy block without pubdeploy installed fails check

  Scenario: A repository without a deploy block needs no pubdeploy
    Then a repository without a deploy block needs no pubdeploy

  Scenario: Bump runs the bump hooks before sync writes the launchers
    Then bump runs the bump hooks before sync writes the launchers

  Scenario: The removed commands are gone
    Then the removed commands are gone

  Scenario: The nightly runs the bump hooks and syncs what they moved
    Then the nightly runs the bump hooks and syncs what they moved

  Scenario: The nightly with nothing moved neither syncs nor locks
    Then the nightly with nothing moved neither syncs nor locks
