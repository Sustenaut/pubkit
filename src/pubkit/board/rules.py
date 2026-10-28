"""Grades from facts: rules.json applied to what the connectors read.

Nothing here reads a source. `grade` takes one repository's facts, each
`{"value": ..., "sources": [call ids], "note": ...}`, and returns its three
grades, each a start and a list of steps, so the page can show how every
letter was reached and which call each step rests on.

The facts, by name:

- `visibility`: "public" or "private"; `kind`: "package" or "service"
- `integration`: `{"pubkit": bool, "pubrel": bool}`
- `pins` and `latest`: `{"pubkit": [versions] or "self", "pubrel": ...}`,
  and the latest release of each, `{"pubkit": "0.14.2", "pubrel": ...}`
- `released`: the newest release (a package) or production deploy (a
  service), `{"at": ISO time, "tag": ...}`, or None
- `issues`, `dependabot_alerts`, `code_scanning_alerts`,
  `secret_scanning_alerts`: counts
- `ci`: main's last CI run, `{"workflow", "conclusion", "at", "jobs":
  [{"name", "conclusion"}]}`, or None
- `nightly`: `{"governed": bool, "last": run or None}`
- `live`: what pubkit.json's live block declares, `{"health": URL,
  "test": workflow, "deploy": workflow}`; `health`: `{"answers": bool,
  "status": int}`; `live_test`: the test workflow's last run, or None
- `protection`: `{"differences": [text], "limit": bool}`
- `sonar`: `{"reliability", "maintainability", "security"}` letters, or
  None; `scorecard`: `{"score", "failing"}`, or None

A fact that could not be read has the value None and a note. Where that
leaves a notch's question open (issues, CI, protection), the notch is
taken: a board that cannot see does not grade green. Alerts and Scorecard
that are switched off or absent are listed with no notch.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Any

LETTERS = "ABCDE"
CATEGORIES = ("quality", "security", "delivery")
# The job conclusions that mean a tool found something.
FAILED = {"failure", "timed_out", "startup_failure"}


def load(path: Path | None = None) -> dict:
    """rules.json: the file given, or the one pubkit ships."""
    text = path.read_text(encoding="utf-8") if path else \
        resources.files("pubkit.board").joinpath("rules.json").read_text(encoding="utf-8")
    rules = json.loads(text)
    unknown = [d["notch"] for d in rules.get("delivery", []) if d.get("notch") not in DELIVERY]
    if unknown:
        raise ValueError(f"no delivery notch {', '.join(map(str, unknown))}; "
                         f"the notches are {', '.join(DELIVERY)}")
    for tool in rules.get("tools", []):
        if tool.get("category") not in ("quality", "security"):
            raise ValueError(f"tool {tool.get('tool')}: category must be quality or security")
    return rules


def when(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None


class Facts:
    """One repository's facts, read by name."""

    def __init__(self, facts: dict[str, Any]):
        self.facts = facts

    def value(self, name: str) -> Any:
        return (self.facts.get(name) or {}).get("value")

    def sources(self, *names: str) -> list[str]:
        out: list[str] = []
        for name in names:
            out += [s for s in (self.facts.get(name) or {}).get("sources", []) if s not in out]
        return out

    def note(self, name: str) -> str | None:
        return (self.facts.get(name) or {}).get("note")

    def known(self, name: str) -> bool:
        return name in self.facts and self.value(name) is not None


def step(text: str, delta: int, facts: Facts, *names: str) -> dict:
    return {"text": text, "delta": delta, "facts": list(names), "sources": facts.sources(*names)}


def letter(start: str, steps: list[dict]) -> str:
    down = -sum(s["delta"] for s in steps)
    return LETTERS[min(LETTERS.index(start) + down, len(LETTERS) - 1)]


def worse(*letters: str) -> str:
    return max(letters, key=LETTERS.index)


def short(job: str) -> str:
    """"ci / test (ubuntu-latest)" -> "test"."""
    name = job.rsplit(" / ", 1)[-1]
    if name.endswith(")") and " (" in name:
        name = name[:name.rindex(" (")]
    return name.strip()


# Sonar's starting letter, per category, from its ratings.
def sonar_start(f: Facts, category: str, rules: dict) -> tuple[dict, list[dict]]:
    ratings = f.value("sonar")
    if not ratings:
        return ({"letter": "A", "text": "Start at A", "sources": []},
                [step(f"{rules.get('sonar', 'No SonarQube Cloud analysis')}"
                      f"{': ' + f.note('sonar') if f.note('sonar') else ''}", -1, f, "sonar")])
    if category == "quality":
        start = worse(ratings["reliability"], ratings["maintainability"])
        text = (f"SonarQube Cloud: reliability {ratings['reliability']}, "
                f"maintainability {ratings['maintainability']}")
    else:
        start = ratings["security"]
        text = f"SonarQube Cloud: security {start}"
    return {"letter": start, "text": text, "sources": f.sources("sonar")}, []


def tool_steps(f: Facts, category: str, rules: dict) -> list[dict]:
    out = []
    ci = f.value("ci")
    jobs = (ci or {}).get("jobs") or []
    for tool in rules.get("tools", []):
        if tool["category"] != category:
            continue
        name = tool["tool"]
        if "jobs" in tool:
            failed = sorted({j["name"] for j in jobs
                             if short(j["name"]) in tool["jobs"] and j.get("conclusion") in FAILED})
            if failed:
                out.append(step(f"{name} has findings on main: {', '.join(failed)} failed",
                                -1, f, "ci"))
        elif "alerts" in tool:
            fact = tool["alerts"]
            count = f.value(fact)
            if count is None:
                if fact in f.facts:
                    out.append(step(f"{name} alerts not read: {f.note(fact) or 'unknown'}",
                                    0, f, fact))
            elif count:
                out.append(step(f"{count} open {name} alert{'s' if count != 1 else ''}",
                                -1, f, fact))
        elif "scorecard_below" in tool:
            if f.value("visibility") != "public":
                continue
            card = f.value("scorecard")
            if not card or card.get("score") is None:
                if "scorecard" in f.facts:
                    out.append(step(f"{name}: {f.note('scorecard') or 'no score'}", 0, f,
                                    "scorecard"))
            elif card["score"] < tool["scorecard_below"]:
                failing = card.get("failing") or []
                extra = f"; {', '.join(failing[:4])}{' …' if len(failing) > 4 else ''}" \
                    if failing else ""
                out.append(step(f"{name} {card['score']}, below {tool['scorecard_below']}{extra}",
                                -1, f, "scorecard"))
    return out


# Delivery notches: each takes the facts, its rules.json entry and the
# build's time, and returns its steps (none when there is no gap).
Notch = Callable[[Facts, dict, datetime], list[dict]]


def pins(f: Facts, _: dict, __: datetime) -> list[dict]:
    pinned, latest = f.value("pins") or {}, f.value("latest") or {}
    behind = []
    for tool in ("pubkit", "pubrel"):
        versions = pinned.get(tool)
        if versions == "self" or not versions:
            continue
        if not latest.get(tool):
            return [step(f"Latest {tool} release not read", -1, f, "latest")]
        old = [v for v in versions if v != latest[tool]]
        if old:
            behind.append(f"{tool} {', '.join(old)}, latest {latest[tool]}")
    return [step("; ".join(behind), -1, f, "pins", "latest")] if behind else []


def integration(f: Facts, _: dict, __: datetime) -> list[dict]:
    have = f.value("integration") or {}
    missing = ["pubkit"] if not have.get("pubkit") else []
    if f.value("kind") != "service" and not have.get("pubrel"):
        missing.append("pubrel")
    if not missing:
        return []
    return [step(f"Not integrated with {' and '.join(missing)}", -1, f, "integration")]


def observability(f: Facts, _: dict, __: datetime) -> list[dict]:
    declared = f.value("live") or {}
    if not declared.get("health"):
        return [step("No health URL in pubkit.json's live block", -1, f, "live")]
    health = f.value("health") or {}
    if health.get("answers"):
        return []
    status = health.get("status")
    why = f"answered {status}" if status else (f.note("health") or "did not answer")
    return [step(f"{declared['health']} {why}", -1, f, "health")]


def live_testing(f: Facts, entry: dict, now: datetime) -> list[dict]:
    declared = f.value("live") or {}
    if not declared.get("test"):
        return [step("No live-test workflow in pubkit.json's live block", -1, f, "live")]
    last = f.value("live_test")
    hours = entry.get("hours", 24)
    if not last:
        why = f": {f.note('live_test')}" if f.note("live_test") else ""
        return [step(f"{declared['test']} has not run{why}", -1, f, "live_test")]
    if last.get("conclusion") != "success":
        return [step(f"{declared['test']}'s last run is {last.get('conclusion')}", -1, f, "live_test")]
    if now - when(last["at"]) > timedelta(hours=hours):
        return [step(f"{declared['test']} last passed {last['at'][:16].replace('T', ' ')}, "
                     f"over {hours} h ago", -1, f, "live_test")]
    return []


def release(f: Facts, entry: dict, now: datetime) -> list[dict]:
    days = entry.get("days", 61)
    last = f.value("released")
    what = "production deploy" if f.value("kind") == "service" else "release"
    if not last:
        return [step(f"No {what}{': ' + f.note('released') if f.note('released') else ''}",
                     -1, f, "released")]
    if now - when(last["at"]) > timedelta(days=days):
        label = last.get("tag") or what
        return [step(f"Last {what} {label} on {last['at'][:10]}, over {days} days ago",
                     -1, f, "released")]
    return []


def issues(f: Facts, _: dict, __: datetime) -> list[dict]:
    if not f.known("issues"):
        return [step(f"Open issues not read: {f.note('issues') or 'unknown'}", -1, f, "issues")]
    count = f.value("issues")
    return [step(f"{count} open issue{'s' if count != 1 else ''}", -1, f, "issues")] if count else []


def nightly(f: Facts, entry: dict, now: datetime) -> list[dict]:
    found = f.value("nightly") or {}
    if found.get("governed"):
        return []
    last = found.get("last")
    days = entry.get("days", 2)
    if last and now - when(last["at"]) <= timedelta(days=days):
        return []
    return [step(f"Not in governed.json, and no nightly run in {days} days", -1, f, "nightly")]


def protection(f: Facts, _: dict, __: datetime) -> list[dict]:
    if not f.known("protection"):
        return [step(f"Protection not read: {f.note('protection') or 'unknown'}", -1, f,
                     "protection")]
    found = f.value("protection")
    differences = found.get("differences") or []
    if not differences:
        return []
    if found.get("limit"):
        return [step(f"No ruleset: {differences[0]} (a GitHub plan limit)", -1, f, "protection")]
    more = f" (and {len(differences) - 1} more)" if len(differences) > 1 else ""
    return [step(f"Protection differs from pubkit's: {differences[0]}{more}", -1, f, "protection")]


def ci(f: Facts, _: dict, __: datetime) -> list[dict]:
    run = f.value("ci")
    if not run:
        return [step(f"No CI run on main{': ' + f.note('ci') if f.note('ci') else ''}", -1, f, "ci")]
    if run.get("conclusion") != "success":
        return [step(f"Main's last CI run ({run.get('workflow')}) is {run.get('conclusion')}",
                     -1, f, "ci")]
    return []


DELIVERY: dict[str, Notch] = {
    "pins": pins, "integration": integration, "observability": observability,
    "live-testing": live_testing, "release": release, "issues": issues, "nightly": nightly,
    "protection": protection, "ci": ci,
}


def applies(entry: dict, kind: str | None) -> bool:
    scope = entry.get("applies", "all")
    return scope == "all" or (scope == "service") == (kind == "service")


def grade(facts: dict[str, Any], rules: dict, now: datetime) -> dict:
    """One repository's grades: each a start, its steps and its letter;
    the overall letter, the worst of them; and how many notches it took."""
    f = Facts(facts)
    out: dict[str, Any] = {}
    for category in ("quality", "security"):
        start, steps = sonar_start(f, category, rules)
        steps += tool_steps(f, category, rules)
        out[category] = {"start": start, "steps": steps}
    steps = []
    for entry in rules.get("delivery", []):
        if applies(entry, f.value("kind")):
            steps += DELIVERY[entry["notch"]](f, entry, now)
    out["delivery"] = {"start": {"letter": "A", "text": "Start at A", "sources": []},
                       "steps": steps}
    for category in CATEGORIES:
        g = out[category]
        g["letter"] = letter(g["start"]["letter"], g["steps"])
        g["notches"] = -sum(s["delta"] for s in g["steps"])
    return {"grades": out, "overall": worse(*(out[c]["letter"] for c in CATEGORIES)),
            "notches": sum(out[c]["notches"] for c in CATEGORIES)}


def order(rows: list[dict]) -> list[dict]:
    """Worst first: by overall letter, then most notches, then name."""
    return sorted(rows, key=lambda r: (-LETTERS.index(r["overall"]), -r["notches"], r["repo"]))
