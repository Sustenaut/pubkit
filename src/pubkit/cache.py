"""Keep what a build fetched or made between runs, by rules the repository states.

pubkit.json's `cache` block is one map of named entries, each with the
same keys:

    "cache": {
      "data":     {"path": "data", "refresh": "week"},
      "packs":    {"path": ".packcache", "unit": "data/areas/{unit}/",
                   "inputs": ["data/boundaries.geojsonl", "mapbuild/*.py"],
                   "tools": ["tippecanoe --version"]},
      "overview": {"path": ".packcache/overview",
                   "inputs": ["data/national/", "data/boundaries.geojsonl", "mapbuild/*.py"],
                   "tools": ["tippecanoe --version"]}
    }

- `path`: the file or directory kept between runs.
- `refresh`: `run`, `day`, `week` (ISO week), `month` or `never`: how long
  what is kept stands before it is fetched anew.
- `version`: a free string; changing it fetches anew.
- `inputs`: files, directories or globs what is kept depends on.
- `tools`: commands whose output counts, such as `tippecanoe --version`.
- `unit`: one pattern with `{unit}`, filled from `pubkit cache run
  --unit`, so each unit gets a fingerprint of its own.

An entry needs `refresh` or `inputs` (or both).

**Fetched entries** (`refresh` other than `run`, and no `inputs` or
`unit`) are kept under a key that changes with the period and the version:
the first run of a period fetches them, and later runs restore them. A
fresh run restores none.

**Made entries** (with `inputs`, `unit` or `refresh: run`) are restored
from the latest run and saved under a key of each run. The build makes
them through

    pubkit cache run NAME [--unit VALUE] --output FILE ... -- COMMAND

which fingerprints them (the entry's name, the unit, the `unit` pattern
filled in, every `inputs` entry, every `tools` command's output, and the
version), copies the outputs from `path` when a build with the same
fingerprint made them, and otherwise runs COMMAND, which must write every
`--output`, and keeps what it wrote. Saving the cache drops whatever no run
used since the last save, so `path` holds one build's worth.

`pubkit cache plan` gives the key for each entry, and `actions/cache`
restores and saves them. `plan --refresh` restores no entry at all, fetched
or made, so the run fetches and makes everything anew (`actions/pr-label`
tells a workflow when the pull request it runs for asks for that). Only Python's standard library is used, so the
action runs this module from its own copy of pubkit without installing
anything.
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CONFIG = "pubkit.json"
KEYS = ("path", "refresh", "version", "inputs", "tools", "unit")
REFRESH = ("run", "day", "week", "month", "never")
USED = ".pubkit-used"
# actions/cache gives this many slots: the most entries one cache block takes.
SLOTS = 8


class CacheError(Exception):
    """The cache block or a request does not fit the rules."""


def check(block: object) -> dict:
    """The cache block, refused with a message naming what is wrong."""
    if not isinstance(block, dict):
        raise CacheError(f"{CONFIG}'s cache is a map of named entries")
    if len(block) > SLOTS:
        raise CacheError(f"{CONFIG}'s cache has {len(block)} entries; it takes at most {SLOTS}")
    for name, entry in block.items():
        if not isinstance(entry, dict):
            raise CacheError(f"cache entry {name} is a map of {', '.join(KEYS)}")
        unknown = sorted(set(entry) - set(KEYS))
        if unknown:
            raise CacheError(f"cache entry {name} has no setting {', '.join(unknown)}; "
                             f"it takes {', '.join(KEYS)}")
        if not isinstance(entry.get("path"), str) or not entry["path"]:
            raise CacheError(f"cache entry {name} needs a path: the file or directory kept")
        if "refresh" not in entry and "inputs" not in entry:
            raise CacheError(f"cache entry {name} needs refresh or inputs (or both)")
        if entry.get("refresh", "run") not in REFRESH:
            raise CacheError(f"cache entry {name}: refresh is one of {', '.join(REFRESH)}")
        if "version" in entry and not isinstance(entry["version"], str):
            raise CacheError(f"cache entry {name}: version is a string")
        for key in ("inputs", "tools"):
            items = entry.get(key, [])
            if not isinstance(items, list) or not all(isinstance(e, str) for e in items):
                raise CacheError(f"cache entry {name}: {key} is a list of strings")
        if "unit" in entry and (not isinstance(entry["unit"], str) or "{unit}" not in entry["unit"]):
            raise CacheError(f"cache entry {name}: unit is one pattern with {{unit}} in it")
    return block


def rules(root: Path) -> dict:
    """pubkit.json's cache block, checked."""
    path = root / CONFIG
    if not path.is_file():
        raise CacheError(f"no {CONFIG} here")
    block = json.loads(path.read_text(encoding="utf-8")).get("cache")
    if block is None:
        raise CacheError(f"{CONFIG} has no cache block")
    return check(block)


def fetched(entry: dict) -> bool:
    """Whether an entry is kept for a period rather than made by the build."""
    return entry.get("refresh", "run") != "run" and "unit" not in entry and "inputs" not in entry


def period(refresh: str, today: dt.date) -> str:
    """The part of a fetched entry's key that changes when it should be fetched anew."""
    if refresh == "day":
        return today.isoformat()
    if refresh == "week":
        year, week, _ = today.isocalendar()
        return f"{year}-W{week:02d}"
    if refresh == "month":
        return today.strftime("%Y-%m")
    return "kept"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def plan(root: Path, today: dt.date, run_id: str) -> list[dict]:
    """For each entry, in name order: its path and its actions/cache key. A
    fetched entry's key changes with its period and version and is restored
    exactly; a made entry's key is new each run and is restored from the
    latest by its prefix."""
    out = []
    for name, entry in sorted(rules(root).items()):
        prefix = f"pubkit-cache-{name}-{_digest(json.dumps(entry, sort_keys=True))}-"
        if fetched(entry):
            key = prefix + period(entry["refresh"], today) + "-" + _digest(entry.get("version", ""))
            out.append({"name": name, "path": entry["path"], "key": key, "prefix": "", "fetched": True})
        else:
            out.append({"name": name, "path": entry["path"], "key": prefix + run_id, "prefix": prefix,
                        "fetched": False})
    return out


def _entry(root: Path, entry: str, h) -> None:
    """Feed one file, directory or glob into the fingerprint `h`."""
    # Each file is named relative to its entry, which may lie outside the
    # repository (an absolute path, or a unit value naming one).
    if any(c in entry for c in "*?["):
        base = root
        files = sorted(Path(p) for p in glob.glob(str(root / entry), recursive=True))
    else:
        path = root / entry
        if path.is_dir():
            base = path
            files = sorted(p for p in path.rglob("*") if p.is_file())
        elif path.is_file():
            base = path.parent
            files = [path]
        else:
            h.update(f"missing {entry}\0".encode())
            return
    h.update(f"entry {entry}\0".encode())
    for f in files:
        if f.is_file():
            name = Path(os.path.relpath(f, base)).as_posix()
            h.update(name.encode() + b"\0" + f.read_bytes() + b"\0")


def fingerprint(root: Path, name: str, entry: dict, unit: str | None) -> str:
    """What one unit of a made entry depends on, as a hex digest."""
    h = hashlib.sha256(f"entry {name}\0version {entry.get('version', '')}\0".encode())
    if "unit" in entry:
        if unit is None:
            raise CacheError(f"cache entry {name} is made per unit: give --unit")
        h.update(f"unit {unit}\0".encode())
        _entry(root, entry["unit"].replace("{unit}", unit), h)
    elif unit is not None:
        raise CacheError(f"cache entry {name} has no unit pattern, so it takes no --unit")
    for item in entry.get("inputs", []):
        _entry(root, item, h)
    for command in entry.get("tools", []):
        out = subprocess.run(command, shell=True, cwd=root, capture_output=True, text=True)
        h.update(f"tool {command}\0{out.returncode}\0{out.stdout}\0{out.stderr}\0".encode())
    return h.hexdigest()[:32]


def run(root: Path, name: str, unit: str | None, outputs: list[Path], command: list[str]) -> str:
    """Copy `outputs` from the cache when a build with this fingerprint made
    them, else run `command` and keep them. Answers "reused" or "built"."""
    block = rules(root)
    if name not in block:
        raise CacheError(f"{CONFIG}'s cache has no entry {name}")
    entry = block[name]
    if fetched(entry):
        raise CacheError(f"cache entry {name} is fetched for a period, not made: it has no inputs or unit")
    if not outputs:
        raise CacheError("give each file the command writes as --output")
    store = root / entry["path"]
    key = fingerprint(root, name, entry, unit)
    kept = store / key
    files = [kept / f"{i}-{o.name}" for i, o in enumerate(outputs)]
    store.mkdir(parents=True, exist_ok=True)
    with (store / USED).open("a", encoding="utf-8") as used:
        used.write(key + "\n")
    if all(f.is_file() for f in files):
        for f, o in zip(files, outputs):
            o.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, o)
        return "reused"
    if not command:
        raise CacheError(f"no {name} kept for this unit, and no command to make it after --")
    # The command's output goes to stderr, so stdout carries only the verdict.
    done = subprocess.run(command, cwd=root, stdout=sys.stderr)
    if done.returncode:
        raise CacheError(f"the command for {name} failed (exit {done.returncode}); nothing kept")
    missing = [str(o) for o in outputs if not o.is_file()]
    if missing:
        raise CacheError(f"the command for {name} did not write {', '.join(missing)}; nothing kept")
    fresh = Path(tempfile.mkdtemp(dir=store, prefix=".new-"))
    for i, o in enumerate(outputs):
        shutil.copyfile(o, fresh / f"{i}-{o.name}")
    if kept.exists():
        shutil.rmtree(kept)
    fresh.rename(kept)
    return "built"


def prune(root: Path) -> list[str]:
    """Drop what no run used since the last prune, in each made entry a run
    used. An entry no run used this time is left as it is."""
    report = []
    for name, entry in sorted(rules(root).items()):
        store = root / entry["path"]
        used_file = store / USED
        if fetched(entry) or not used_file.is_file():
            continue
        used = set(used_file.read_text(encoding="utf-8").split())
        removed = 0
        for child in store.iterdir():
            # A made entry's path may hold another's (.packcache holds
            # .packcache/overview): only fingerprints are this entry's.
            if child.is_dir() and len(child.name) == 32 and child.name not in used:
                shutil.rmtree(child)
                removed += 1
        used_file.unlink()
        report.append(f"{name}: kept {len(used)}, removed {removed}")
    return report


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pubkit cache", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="step", required=True)
    p = commands.add_parser("plan", help="each entry's path and actions/cache key")
    p.add_argument("--github-output", action="store_true", help="also append them to $GITHUB_OUTPUT")
    p.add_argument("--fresh", action="store_true", help="restore no fetched entry")
    p.add_argument("--refresh", action="store_true", help="restore no entry, fetched or made")
    p = commands.add_parser("run", help="reuse a unit's outputs, or make and keep them: "
                                        "run NAME [--unit VALUE] --output FILE... -- COMMAND")
    p.add_argument("name")
    p.add_argument("--unit")
    p.add_argument("--output", action="append", default=[], type=Path, metavar="FILE")
    commands.add_parser("prune", help="drop what no run used since the last prune "
                                      "(actions/cache's save step does this)")
    argv = sys.argv[1:] if argv is None else list(argv)
    # Everything after -- is the command, given to it as it is.
    command = argv[argv.index("--") + 1:] if "--" in argv else []
    args = parser.parse_args(argv[:argv.index("--")] if "--" in argv else argv)
    root = root or Path.cwd()
    try:
        if args.step == "plan":
            today = dt.date.fromisoformat(os.environ["PUBKIT_TODAY"]) if os.environ.get("PUBKIT_TODAY") \
                else dt.datetime.now(dt.UTC).date()
            run_id = (f"{os.environ['GITHUB_RUN_ID']}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
                      if os.environ.get("GITHUB_RUN_ID") else dt.datetime.now(dt.UTC).strftime("%Y%m%d%H%M%S"))
            slots = plan(root, today, run_id)
            for s in slots:
                print(f"{s['name']}: path={s['path']} key={s['key']}"
                      + (f" restore-prefix={s['prefix']}" if s["prefix"] else "")
                      + (" (refresh: not restored)" if args.refresh
                         else " (fresh: not restored)" if args.fresh and s["fetched"] else ""))
            if args.github_output:
                with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as gh:
                    for i in range(1, SLOTS + 1):
                        s = slots[i - 1] if i <= len(slots) else None
                        restore = bool(s) and not args.refresh and not (args.fresh and s["fetched"])
                        gh.write(f"path-{i}={s['path'] if s else ''}\n")
                        gh.write(f"key-{i}={s['key'] if s else ''}\n")
                        gh.write(f"prefix-{i}={s['prefix'] if s else ''}\n")
                        gh.write(f"restore-{i}={'true' if restore else 'false'}\n")
            return 0
        if args.step == "run":
            verdict = run(root, args.name, args.unit,
                          [o if o.is_absolute() else root / o for o in args.output], command)
            print(f"{verdict} {args.name}" + (f" {args.unit}" if args.unit is not None else ""))
            return 0
        for line in prune(root):
            print(line)
        return 0
    except CacheError as e:
        print(f"pubkit cache: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
