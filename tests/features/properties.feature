Feature: What holds for every input, tried by Hypothesis
  Each scenario states a property and lets Hypothesis generate the inputs
  that would break it, PUBRULES_FUZZ_RUNS of them: a few on every push, many
  on the nightly schedule. The action is Hypothesis trying them; a refusal
  would carry the smallest input that broke the property.

  Scenario: Canonical CBOR decodes back to every value it encodes
    When Hypothesis encodes each value pub read --json shows to CBOR and decodes it back
    Then it is not refused

  Scenario: Canonical CBOR is the same whatever order a map's keys come in
    When Hypothesis encodes each map with its keys in any order
    Then it is not refused

  Scenario: The nightly lists exactly the packages whose version moved
    When Hypothesis lists the packages changed between any two locks
    Then it is not refused
