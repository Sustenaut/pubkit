Feature: pubkit cache keeps what a build fetched or made, by the repository's entries
  pubkit.json's cache block is one map of named entries with the same keys:
  path, refresh, version, inputs, tools and unit. A fetched entry is kept
  for a period. A made entry is kept under a fingerprint of what made it
  (the unit's files, the inputs, the tools' output and the version), and a
  unit whose fingerprint is unchanged is copied, not made again.

  Background:
    Given a repository whose cache entry "packs" is made per unit from "data/{unit}/" with the input "build.py" and the tool "cat tool.txt"
    And the file "data/north/roads.txt" reads "north roads"
    And the file "data/south/roads.txt" reads "south roads"
    And the file "build.py" reads "version 1"

  Scenario: A unit is made once, then reused while nothing it depends on changes
    When I make the pack of "north"
    Then it succeeds
    And it prints "built packs north"
    And the pack of "north" holds "north roads"
    When I make the pack of "north"
    Then it prints "reused packs north"
    And the command ran 1 time(s)

  Scenario: A change to a unit's own files makes only that unit again
    When I make the pack of "north"
    And I make the pack of "south"
    And the file "data/south/roads.txt" reads "south roads, resurfaced"
    And I make the pack of "north"
    Then it prints "reused packs north"
    When I make the pack of "south"
    Then it prints "built packs south"
    And the pack of "south" holds "south roads, resurfaced"

  Scenario: A change to an input, a tool's output or the version makes every unit again
    When I make the pack of "north"
    And the file "build.py" reads "version 2"
    And I make the pack of "north"
    Then it prints "built packs north"
    When the tool reports version "2"
    And I make the pack of "north"
    Then it prints "built packs north"
    When the cache entry "packs" has the version "b"
    And I make the pack of "north"
    Then it prints "built packs north"

  Scenario: A unit's files may lie outside the repository
    Given the cache entry "packs" is made per unit from "../elsewhere/{unit}/"
    And the file "elsewhere/north/roads.txt" outside the repository reads "far roads"
    When I make the pack of "north" from "elsewhere"
    Then it succeeds
    And it prints "built packs north"
    When I make the pack of "north" from "elsewhere"
    Then it prints "reused packs north"

  Scenario: prune keeps only what runs since the last prune used
    When I make the pack of "north"
    And I make the pack of "south"
    And I run pubkit "cache" "prune"
    And I make the pack of "north"
    And I run pubkit "cache" "prune"
    Then it prints "packs: kept 1, removed 1"

  Scenario: A command that fails, or does not write its output, keeps nothing
    When I run pubkit "cache" "run" "packs" "--unit" "north" "--output" "out/north.pack" "--" "false"
    Then the exit code is 2
    And stderr mentions "the command for packs failed (exit 1); nothing kept"
    When I run pubkit "cache" "run" "packs" "--unit" "north" "--output" "out/north.pack" "--" "true"
    Then the exit code is 2
    And stderr mentions "did not write"

  Scenario: A made-per-unit entry needs --unit
    When I run pubkit "cache" "run" "packs" "--output" "out/x.pack" "--" "true"
    Then the exit code is 2
    And stderr mentions "cache entry packs is made per unit: give --unit"

  Scenario: Each entry gets its own key: a fetched one by its period, a made one by the run
    Given the cache entry "data" keeps "data" with refresh "week"
    And today is "2026-10-29"
    When I run pubkit "cache" "plan"
    Then it prints "data: path=data key=pubkit-cache-data-"
    And it prints "packs: path=.packcache key=pubkit-cache-packs-"
    And I note the key of "data"
    Given today is "2026-11-01"
    When I run pubkit "cache" "plan"
    Then the key of "data" is as noted
    Given today is "2026-11-02"
    When I run pubkit "cache" "plan"
    Then the key of "data" is not as noted

  Scenario: An entry with an unknown setting fails pubkit sync, naming it
    Given the cache entry "packs" has the setting "dir"
    When I run pubkit "sync" "--check"
    Then it fails
    And stderr mentions "cache entry packs has no setting dir"

  Scenario: An entry with neither refresh nor inputs is refused
    Given the cache entry "loose" keeps "loose" with no refresh or inputs
    When I run pubkit "cache" "plan"
    Then the exit code is 2
    And stderr mentions "cache entry loose needs refresh or inputs (or both)"
