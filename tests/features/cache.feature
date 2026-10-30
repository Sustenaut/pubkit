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

  Scenario: A refresh run restores no entry, fetched or made
    Given the cache entry "data" keeps "data" with refresh "week"
    When I run pubkit "cache" "plan" "--refresh"
    Then it prints "data: path=data key=pubkit-cache-data-"
    And it prints "(refresh: not restored)"
    And it prints "packs: path=.packcache key=pubkit-cache-packs-"

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

  Scenario Outline: A cache block that does not fit is refused, naming what is wrong
    Given the cache block is <block>
    When I run pubkit "cache" "plan"
    Then the exit code is 2
    And stderr mentions "<reason>"

    Examples:
      | block | reason |
      | [] | cache is a map of named entries |
      | {"a": 1} | cache entry a is a map of |
      | {"a": {"refresh": "week"}} | cache entry a needs a path |
      | {"a": {"path": "a", "refresh": "hourly"}} | refresh is one of run, day, week, month, never |
      | {"a": {"path": "a", "refresh": "week", "version": 1}} | version is a string |
      | {"a": {"path": "a", "inputs": "b"}} | inputs is a list of strings |
      | {"a": {"path": "a", "inputs": ["b"], "unit": "data/"}} | unit is one pattern with {unit} in it |
      | {"a": {"path": "a", "refresh": "week"}, "b": {"path": "b", "refresh": "week"}, "c": {"path": "c", "refresh": "week"}, "d": {"path": "d", "refresh": "week"}, "e": {"path": "e", "refresh": "week"}, "f": {"path": "f", "refresh": "week"}, "g": {"path": "g", "refresh": "week"}, "h": {"path": "h", "refresh": "week"}, "i": {"path": "i", "refresh": "week"}} | it takes at most 8 |

  Scenario Outline: A run that does not fit the entries is refused
    Given the cache entry "data" keeps "data" with refresh "week"
    And the cache entry "notes" is made from the input "notes.txt"
    When I run pubkit <args>
    Then the exit code is 2
    And stderr mentions "<reason>"

    Examples:
      | args | reason |
      | "cache" "run" "nonesuch" "--output" "x" "--" "true" | cache has no entry nonesuch |
      | "cache" "run" "data" "--output" "x" "--" "true" | data is fetched for a period, not made |
      | "cache" "run" "packs" "--unit" "north" "--" "true" | give each file the command writes as --output |
      | "cache" "run" "notes" "--unit" "x" "--output" "x" "--" "true" | has no unit pattern, so it takes no --unit |
      | "cache" "run" "notes" "--output" "x" | no notes kept for this unit, and no command to make it |

  Scenario: An input glob counts every file it matches, and a missing input counts as missing
    Given the cache entry "notes" is made from the inputs "*.md" and "later/"
    And the file "a.md" reads "one"
    When I make "notes" into "out/notes.txt"
    Then it prints "built notes"
    When I make "notes" into "out/notes.txt"
    Then it prints "reused notes"
    When the file "b.md" reads "two"
    And I make "notes" into "out/notes.txt"
    Then it prints "built notes"
    When the file "later/x.txt" reads "now here"
    And I make "notes" into "out/notes.txt"
    Then it prints "built notes"

  Scenario: Day and month periods give keys that change daily and monthly
    Given the cache entry "daily" keeps "daily" with refresh "day"
    And the cache entry "monthly" keeps "monthly" with refresh "month"
    And today is "2026-10-29"
    When I run pubkit "cache" "plan"
    Then it prints "-2026-10-29-"
    And it prints "-2026-10-"

  Scenario: plan writes a restore and a save slot for each entry to GitHub's step output
    Given the cache entry "data" keeps "data" with refresh "week"
    And GitHub's step output goes to "gh-output"
    When I run pubkit "cache" "plan" "--github-output" "--fresh"
    Then it succeeds
    And "gh-output" has the line "path-1=data"
    And "gh-output" has the line "restore-1=false"
    And "gh-output" has the line "path-2=.packcache"
    And "gh-output" has the line "restore-2=true"
    And "gh-output" has the line "key-8="
