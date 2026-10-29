Feature: pubkit cache keeps what a build fetched or made, by the repository's rules
  pubkit.json's cache block names inputs, kept for a period, and outputs,
  made once per unit and kept under a fingerprint of what made them: the
  unit's own files, files every unit shares, and tool versions. A unit
  whose fingerprint is unchanged is copied, not made again.

  Background:
    Given a repository whose cache rules make one pack per area from "data/{area}/" with the shared "build.py"
    And the file "data/north/roads.txt" reads "north roads"
    And the file "data/south/roads.txt" reads "south roads"
    And the file "build.py" reads "version 1"

  Scenario: A unit is made once, then reused while nothing it depends on changes
    When I make the pack of "north"
    Then it succeeds
    And it prints "built packs area=north"
    And the pack of "north" holds "north roads"
    When I make the pack of "north"
    Then it prints "reused packs area=north"
    And the command ran 1 time(s)

  Scenario: A change to a unit's own files makes only that unit again
    When I make the pack of "north"
    And I make the pack of "south"
    And the file "data/south/roads.txt" reads "south roads, resurfaced"
    And I make the pack of "north"
    Then it prints "reused packs area=north"
    When I make the pack of "south"
    Then it prints "built packs area=south"
    And the pack of "south" holds "south roads, resurfaced"

  Scenario: A change to a shared file or a tool's version makes every unit again
    When I make the pack of "north"
    And the file "build.py" reads "version 2"
    And I make the pack of "north"
    Then it prints "built packs area=north"
    When the tool reports version "2"
    And I make the pack of "north"
    Then it prints "built packs area=north"

  Scenario: prune keeps only what runs since the last prune used
    When I make the pack of "north"
    And I make the pack of "south"
    And I run pubkit "cache" "prune"
    And I make the pack of "north"
    And I run pubkit "cache" "prune"
    Then it prints "packs: kept 1, removed 1"

  Scenario: A command that fails, or does not write its output, keeps nothing
    When I run pubkit "cache" "run" "packs" "--unit" "area=north" "--output" "out/north.pack" "--" "false"
    Then the exit code is 2
    And stderr mentions "the command for packs failed (exit 1); nothing kept"
    When I run pubkit "cache" "run" "packs" "--unit" "area=north" "--output" "out/north.pack" "--" "true"
    Then the exit code is 2
    And stderr mentions "did not write"

  Scenario: A unit's files may lie outside the repository
    Given the cache rules make one pack per area from "{root}/{area}/" instead
    And the file "elsewhere/north/roads.txt" outside the repository reads "far roads"
    When I make the pack of "north" from "elsewhere"
    Then it succeeds
    And it prints "built packs"
    When I make the pack of "north" from "elsewhere"
    Then it prints "reused packs"

  Scenario: An entry naming a unit value the run does not give is refused
    When I run pubkit "cache" "run" "packs" "--output" "out/x.pack" "--" "true"
    Then the exit code is 2
    And stderr mentions "data/{area}/ needs --unit area=..."

  Scenario: plan keys inputs by their period, and outputs by the run
    Given the cache rules keep "data" for a "week"
    And today is "2026-10-29"
    When I run pubkit "cache" "plan"
    Then it prints "inputs-paths=data"
    And it prints "outputs-paths=.packcache"
    And I note the inputs key
    Given today is "2026-11-01"
    When I run pubkit "cache" "plan"
    Then the inputs key is as noted
    Given today is "2026-11-02"
    When I run pubkit "cache" "plan"
    Then the inputs key is not as noted

  Scenario: Rules with an unknown setting are refused
    Given the cache rules have the setting "outputs.packs.speed"
    When I run pubkit "cache" "prune"
    Then the exit code is 2
    And stderr mentions "cache output packs takes dir, unit and shared"
