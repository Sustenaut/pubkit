Feature: What holds for every input, tried by Hypothesis
  Each scenario states a property and lets Hypothesis generate the inputs
  that would break it, PUBKIT_FUZZ_RUNS of them: a few on every push, many
  on the nightly schedule.

  Scenario: Canonical CBOR decodes back to every value it encodes
    Then every value pub read --json shows encodes to CBOR that decodes back to it

  Scenario: Canonical CBOR is the same whatever order a map's keys come in
    Then every map encodes to the same CBOR with its keys in any order, sorted by their bytes

  Scenario: The nightly lists exactly the packages whose version moved
    Then for any two locks, the packages listed as changed are those in both whose version differs
