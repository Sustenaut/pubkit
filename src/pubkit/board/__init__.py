"""`pubkit board`: grade every repository a repos file names, A to E, for
quality, security and delivery, into board.json and a static index.html.

It reads the repositories from the repos file (or, until they go, from
protected.json and governed.json), reads each from GitHub, GitHub Actions, SonarQube Cloud, OpenSSF Scorecard and
the services' health URLs through one connector per source
(connectors/), turns what they read into facts (collect.py), and grades
the facts by rules.json (rules.py). It writes nothing anywhere else and
keeps nothing between builds: board.json holds every call made, and links
each fact and each notch to the calls it rests on.

GITHUB_TOKEN (or GH_TOKEN) is the read-only token it reads GitHub with;
SONAR_TOKEN, when set, reads private SonarQube Cloud projects.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path

from .. import __version__
from . import page, rules as grading
from .collect import Connectors, Gatherer, inventory
from .connectors.actions import Actions
from .connectors.base import Ledger, Network, Recording, Replay, Transport, now, stamp
from .connectors.github import GitHub
from .connectors.live import Live
from .connectors.repos import Repos
from .connectors.scorecard import Scorecard
from .connectors.sonarcloud import SonarCloud


def connectors(ledger: Ledger, transport: Transport) -> Connectors:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None
    return Connectors(
        repos=Repos(ledger, transport), github=GitHub(ledger, transport, token),
        actions=Actions(ledger, transport, token),
        sonarcloud=SonarCloud(ledger, transport, os.environ.get("SONAR_TOKEN") or None),
        scorecard=Scorecard(ledger, transport), live=Live(ledger, transport))


def released(row_facts: dict, kind: str) -> str:
    value = row_facts.get("released", {}).get("value")
    if not value:
        return "-"
    return value.get("tag") or (f"deployed {value['at'][:10]}" if kind == "service" else value["at"][:10])


def pinned(row_facts: dict) -> str:
    pins = (row_facts.get("pins", {}).get("value") or {}).get("pubkit")
    if pins == "self":
        return "itself"
    return ", ".join(pins) if pins else "-"


def summary(rows: list[dict], built: datetime) -> str:
    counts = Counter(r["overall"] for r in rows)
    parts = ", ".join(f"{counts[g]} {g}" for g in grading.LETTERS if counts[g])
    return (f"{len(rows)} repo{'s' if len(rows) != 1 else ''}: {parts or 'none'}. "
            f"Built {built.strftime('%Y-%m-%d %H:%M')} UTC.")


def build(protected: Path | None, governed: Path | None, rules: dict, transport: Transport,
          built: datetime, repos: Path | None = None) -> dict:
    """The board: every repository's facts and grades, and every call."""
    ledger = Ledger(clock=now if isinstance(transport, (Network, Recording)) else (lambda: built))
    c = connectors(ledger, transport)
    items, problems = inventory(c, protected, governed, repos)
    gatherer = Gatherer(c, rules)
    rows = []
    for item in items:
        facts = {name: fact.as_json() for name, fact in gatherer.gather(item).items()}
        graded = grading.grade(facts, rules, built)
        kind = facts["kind"]["value"]
        rows.append({"repo": item["repo"], "visibility": facts["visibility"]["value"] or "unknown",
                     "kind": kind, "released": released(facts, kind), "pubkit": pinned(facts),
                     **({"project": item["project"]} if item.get("project") else {}),
                     **graded, "facts": facts})
    rows = grading.order(rows)
    return {"built": stamp(built), "pubkit": __version__, "rules": rules.get("name", "unnamed"),
            "summary": summary(rows, built), "problems": problems, "repos": rows,
            "calls": ledger.calls}


def run(protected: Path | None, governed: Path | None, out: Path, rules_path: Path | None = None,
        replay: Path | None = None, record: Path | None = None, repos: Path | None = None) -> int:
    try:
        rules = grading.load(rules_path)
    except (OSError, ValueError) as e:
        raise SystemExit(f"pubkit board: the rules: {e}") from e
    if replay:
        transport: Transport = Replay(replay)
        built = transport.recorded_at
    else:
        transport = Recording(Network()) if record else Network()
        built = now()
    board = build(protected, governed, rules, transport, built, repos)
    if record and isinstance(transport, Recording):
        transport.save(record, built)
    out.mkdir(parents=True, exist_ok=True)
    (out / "board.json").write_text(json.dumps(board, indent=1) + "\n", encoding="utf-8")
    (out / "index.html").write_text(page.render(board), encoding="utf-8")
    print(board["summary"])
    for problem in board["problems"]:
        print(f"pubkit board: {problem}")
    for row in board["repos"]:
        grades = " ".join(f"{k[0].upper()}{row['grades'][k]['letter']}"
                          for k in grading.CATEGORIES)
        print(f"{row['overall']}  {row['repo']}  ({grades}, {row['notches']} notches)")
    return 1 if board["problems"] and not board["repos"] else 0
