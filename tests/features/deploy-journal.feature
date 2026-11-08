Feature: The walk's journal, deploy-journal/2, and writing it to storage
  --json prints the walk as a deploy-journal/2 document: each need with its
  grade and whether it was fulfilled, the service with its unit, the walk
  with its mode. --journal URL also writes it to storage: the document as a
  file of the tenant plane, then the unit's head moved to it, If-Match the
  head's ETag, tried once more when another walk moved it first. A journal
  that could not be written is said, and changes no exit code.

  Background:
    Given the example service

  Scenario: --json is a deploy-journal/2 document, with the unit, each need's grade, and nothing fulfilled
    When I run pubkit "deploy" "walk" "--json"
    Then it succeeds
    And the journal is a deploy-journal/2 document
    And the journal's service is the unit "example"
    And the journal's walk is a "check"
    And the journal grades "github.variable:EXAMPLE_SETTING" "propose" and "k8s.secret:example-files" "auto"
    And the journal fulfilled nothing

  Scenario: A fulfilling walk's journal says which needs were fulfilled
    Given the Secret "example-files" is not in the cluster
    When I run pubkit "deploy" "walk" "--fulfil" "--yes" "--json"
    Then it succeeds
    And the journal is a deploy-journal/2 document
    And the journal's walk is a "fulfil"
    And the journal fulfilled "k8s.secret:example-files" only
    And no output holds a value

  Scenario: --journal writes the document, and moves the unit's head to it
    Given storage holds the head "services/example-org/example/example" at "older"
    When I run pubkit "deploy" "walk" "--journal" "https://files.example.eu"
    Then it succeeds
    And storage was given a deploy-journal/2 document as a file of "plane"
    And the head "services/example-org/example/example" was moved If-Match "older"
    And stderr mentions "deploy: journal file-1 written, the head services/example-org/example/example moved to it"
    And no output holds a value

  Scenario: A head another walk moved first is tried once more
    Given storage holds the head "services/example-org/example/example" at "older"
    And another walk moves the head first, once
    When I run pubkit "deploy" "walk" "--journal" "https://files.example.eu"
    Then it succeeds
    And the head was moved twice, the second time If-Match "newer"

  Scenario: A journal storage refuses is said, and the exit code is the walk's
    Given storage refuses every write
    And the Secret "example-files" is not in the cluster
    When I run pubkit "deploy" "walk" "--journal" "https://files.example.eu"
    Then the exit code is 1
    And stderr mentions "deploy: no journal written: storage said"

  Scenario: Without a storage token no journal is written, in one line
    Given there is no storage token
    When I run pubkit "deploy" "walk" "--journal" "https://files.example.eu"
    Then it succeeds
    And stderr mentions "deploy: no journal written: no storage token here"
