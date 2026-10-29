"""Keep what a build fetched or made, by rules the repository states.

pubkit.json's `cache` block names two kinds of thing:

    "cache": {
      "inputs": {
        "data": {"path": "data", "refresh": "week"}
      },
      "outputs": {
        "packs": {
          "dir": ".packcache",
          "unit": ["data/areas/{path}/", "data/boundaries.geojsonl"],
          "shared": ["mapbuild/*.py", "tool: tippecanoe --version"]
        }
      }
    }

- An **input** is something slow to fetch and slow to change, such as
  open data downloaded from an agency. It is kept for a period, `refresh`:
  `day`, `week` (ISO week), `month`, or `never`; `version` adds a string of
  the repository's own, such as a pinned release, so changing it fetches
  anew. A run asked to be fresh restores none.
- An **output** is something a build makes once per unit, such as one map
  pack per area. `pubkit cache run NAME --unit KEY=VALUE ... --output FILE
  ... -- COMMAND` fingerprints the unit by the rules and copies the files
  from `dir` when a build with the same fingerprint made them; otherwise it
  runs COMMAND, which must write every `--output`, and keeps them. A
  fingerprint covers the output's name, the unit's values, and every entry
  of `unit` (with `{KEY}` filled in from the unit) and `shared`: a file, a
  directory (all its files), a glob, or `tool: COMMAND`, the output of a
  command such as a tool's version. A missing path counts as missing, so
  creating it changes the fingerprint.

`pubkit cache prune` removes the outputs no `run` used since the last prune,
so `dir` holds one build's worth. `pubkit cache plan` gives the
`actions/cache` keys and paths the `actions/cache` step of pubkit uses.

Only Python's standard library is used, so the action runs this module from
its own copy of pubkit without installing anything.
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
REFRESH = ("day", "week", "month", "never")
USED = ".used"


class CacheError(Exception):
    """The cache block or a request does not fit the rules."""


def rules(root: Path) -> dict:
    """pubkit.json's cache block, checked."""
    path = root / CONFIG
    if not path.is_file():
        raise CacheError(f"no {CONFIG} here")
    block = json.loads(path.read_text(encoding="utf-8")).get("cache")
    if not isinstance(block, dict):
        raise CacheError(f"{CONFIG} has no cache block")
    unknown = set(block) - {"inputs", "outputs"}
    if unknown:
        raise CacheError(f"{CONFIG}'s cache has no setting {', '.join(sorted(unknown))}; "
                         "it takes inputs and outputs")
    for name, entry in block.get("inputs", {}).items():
        extra = set(entry) - {"path", "refresh", "version"}
        if extra or not isinstance(entry.get("path"), str):
            raise CacheError(f"cache input {name} takes path, refresh and version, with path a string")
        if entry.get("refresh", "week") not in REFRESH:
            raise CacheError(f"cache input {name}: refresh is one of {', '.join(REFRESH)}")
    for name, entry in block.get("outputs", {}).items():
        extra = set(entry) - {"dir", "unit", "shared"}
        if extra or not isinstance(entry.get("dir"), str):
            raise CacheError(f"cache output {name} takes dir, unit and shared, with dir a string")
        for key in ("unit", "shared"):
            if not all(isinstance(e, str) for e in entry.get(key, [])):
                raise CacheError(f"cache output {name}: {key} is a list of strings")
    return block


def period(refresh: str, today: dt.date) -> str:
    """The part of an input's key that changes when it should be fetched anew."""
    if refresh == "day":
        return today.isoformat()
    if refresh == "week":
        year, week, _ = today.isocalendar()
        return f"{year}-W{week:02d}"
    if refresh == "month":
        return today.strftime("%Y-%m")
    return "kept"


def _digest(parts: list[str]) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()[:16]


def plan(root: Path, today: dt.date, run_id: str) -> dict:
    """The keys and paths for actions/cache: one key for every input, which
    changes when any input's period or version does, and a fresh key for the
    outputs each run, restored from the latest by its prefix."""
    block = rules(root)
    inputs = block.get("inputs", {})
    outputs = block.get("outputs", {})
    parts = [f"{n}:{e['path']}:{period(e.get('refresh', 'week'), today)}:{e.get('version', '')}"
             for n, e in sorted(inputs.items())]
    dirs = sorted(n + ":" + e["dir"] for n, e in outputs.items())
    prefix = f"pubkit-outputs-{_digest(dirs)}-"
    return {
        "inputs-paths": "\n".join(e["path"] for _, e in sorted(inputs.items())),
        "inputs-key": f"pubkit-inputs-{_digest(parts)}" if inputs else "",
        "outputs-paths": "\n".join(e["dir"] for _, e in sorted(outputs.items())),
        "outputs-prefix": prefix if outputs else "",
        "outputs-key": f"{prefix}{run_id}" if outputs else "",
    }


def _entry(root: Path, entry: str, h) -> None:
    """Feed one rule entry into the fingerprint `h`."""
    if entry.startswith("tool:"):
        command = entry[len("tool:"):].strip()
        out = subprocess.run(command, shell=True, cwd=root, capture_output=True, text=True)
        h.update(f"tool {command}\0{out.returncode}\0{out.stdout}\0{out.stderr}\0".encode())
        return
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


def fingerprint(root: Path, name: str, output: dict, unit: dict[str, str]) -> str:
    """What a unit of `output` depends on, as a hex digest."""
    h = hashlib.sha256(f"output {name}\0".encode())
    for key, value in sorted(unit.items()):
        h.update(f"unit {key}={value}\0".encode())
    for entry in output.get("unit", []):
        try:
            filled = entry.format(**unit)
        except KeyError as e:
            raise CacheError(f"cache output {name}: {entry} needs --unit {e.args[0]}=...") from None
        _entry(root, filled, h)
    for entry in output.get("shared", []):
        _entry(root, entry, h)
    return h.hexdigest()[:32]


def run(root: Path, name: str, unit: dict[str, str], outputs: list[Path], command: list[str]) -> str:
    """Copy `outputs` from the cache when a build with this fingerprint made
    them, else run `command` and keep them. Answers "reused" or "built"."""
    block = rules(root).get("outputs", {})
    if name not in block:
        raise CacheError(f"{CONFIG}'s cache has no output {name}")
    if not outputs:
        raise CacheError("give each file the command writes as --output")
    store = root / block[name]["dir"]
    key = fingerprint(root, name, block[name], unit)
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
    """Remove each output's entries no run used since the last prune."""
    report = []
    for name, entry in sorted(rules(root).get("outputs", {}).items()):
        store = root / entry["dir"]
        if not store.is_dir():
            continue
        used_file = store / USED
        used = set(used_file.read_text(encoding="utf-8").split()) if used_file.exists() else set()
        removed = 0
        for child in store.iterdir():
            if child.is_dir() and child.name not in used:
                shutil.rmtree(child)
                removed += 1
        used_file.unlink(missing_ok=True)
        report.append(f"{name}: kept {len(used)}, removed {removed}")
    return report


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pubkit cache", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="step", required=True)
    p = commands.add_parser("plan", help="the actions/cache keys and paths for this repository")
    p.add_argument("--github-output", action="store_true", help="also append them to $GITHUB_OUTPUT")
    p = commands.add_parser("run", help="reuse a unit's outputs, or make and keep them: "
                                        "run NAME [--unit KEY=VALUE]... --output FILE... -- COMMAND")
    p.add_argument("name")
    p.add_argument("--unit", action="append", default=[], metavar="KEY=VALUE")
    p.add_argument("--output", action="append", default=[], type=Path, metavar="FILE")
    commands.add_parser("prune", help="remove outputs no run used since the last prune")
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
            out = plan(root, today, run_id)
            for key, value in out.items():
                print(f"{key}={value}" if "\n" not in value else f"{key}=" + value.replace("\n", " "))
            if args.github_output:
                with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as gh:
                    for key, value in out.items():
                        gh.write(f"{key}<<PUBKIT_CACHE\n{value}\nPUBKIT_CACHE\n")
            return 0
        if args.step == "run":
            unit = {}
            for pair in args.unit:
                key, sep, value = pair.partition("=")
                if not sep or not key:
                    raise CacheError(f"--unit takes KEY=VALUE, not {pair!r}")
                unit[key] = value
            verdict = run(root, args.name, unit, [root / o if not o.is_absolute() else o for o in args.output], command)
            print(f"{verdict} {args.name}" + "".join(f" {k}={v}" for k, v in sorted(unit.items())))
            return 0
        for line in prune(root):
            print(line)
        return 0
    except CacheError as e:
        print(f"pubkit cache: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
