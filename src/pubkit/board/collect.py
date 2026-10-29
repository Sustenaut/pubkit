"""Facts about each repository, from the connectors' readings.

This is where readings become the facts rules.py grades: a lock's pin
becomes a version, rulesets become their differences from pubkit's. Every
read goes through a connector, and every fact keeps the ids of the calls
it rests on.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import protect
from ..freshness import PIN
from ..nightly import PUBREL_VERSION
from ..refs import AT, RELEASE_PIN, VERSION
from .connectors.actions import Actions
from .connectors.base import Fact
from .connectors.github import GitHub
from .connectors.lists import Lists
from .connectors.live import Live
from .connectors.scorecard import Scorecard
from .connectors.sonarcloud import SonarCloud

# Any pubkit reusable workflow, the generated job-*.yml included, by
# commit or by tag.
PUBKIT_PIN = re.compile(r"schryer/pubkit/\.github/workflows/[\w.-]+\.ya?ml@" + AT + VERSION)
LIVE_KEYS = {"health", "test", "deploy"}
WORKFLOW_NAME = re.compile(r"[\w.-]+\.ya?ml")


@dataclass
class Connectors:
    lists: Lists
    github: GitHub
    actions: Actions
    sonarcloud: SonarCloud
    scorecard: Scorecard
    live: Live


def merged(*facts: Fact, value: Any, note: str | None = None) -> Fact:
    sources: list[str] = []
    for fact in facts:
        sources += [s for s in fact.sources if s not in sources]
    return Fact(value, tuple(sources), note)


def inventory(c: Connectors, protected: Path, governed: Path | None) -> tuple[list[dict], list[str]]:
    """The repositories to grade, in the lists' order, each with its
    protected.json entry and whether governed.json lists it; and any
    problem reading the lists."""
    problems = []
    listed = c.lists.protected(protected)
    if listed.value is None:
        problems.append(f"{protected}: {listed.note}")
    ruled = c.lists.governed(governed) if governed else Fact([], ())
    if ruled.value is None:
        problems.append(f"{governed}: {ruled.note}")
    out: dict[str, dict] = {}
    for e in listed.value or []:
        out[e["repo"]] = {"repo": e["repo"], "entry": e, "governed": False,
                          "sources": list(listed.sources)}
    for e in ruled.value or []:
        row = out.setdefault(e["repo"], {"repo": e["repo"], "entry": None, "governed": False,
                                         "sources": []})
        row["governed"] = True
        row["sources"] += list(ruled.sources)
    return list(out.values()), problems


class Gatherer:
    """Reads each repository's facts. The latest pubkit and pubrel are
    read once for the whole board."""

    def __init__(self, c: Connectors, rules: dict):
        self.c = c
        self.rules = rules
        self.tools = rules.get("repositories", {})
        self._latest: Fact | None = None

    def latest(self) -> Fact:
        if self._latest is None:
            reads = {tool: self.c.github.latest_release(repo) for tool, repo in self.tools.items()}
            value = {tool: (r.value or {}).get("tag", "").removeprefix("v") or None
                     for tool, r in reads.items()}
            notes = [f"{tool}: {r.note}" for tool, r in reads.items() if r.value is None]
            self._latest = merged(*reads.values(), value=value, note="; ".join(notes) or None)
        return self._latest

    def gather(self, item: dict) -> dict[str, Fact]:
        repo, gh, actions = item["repo"], self.c.github, self.c.actions
        facts: dict[str, Fact] = {}
        about = gh.repository(repo)
        facts["visibility"] = Fact((about.value or {}).get("visibility"), about.sources, about.note)
        branch = (about.value or {}).get("default_branch", "main")

        raw = gh.pubkit_json(repo)
        config, config_note = None, raw.note
        if raw.value is not None:
            try:
                config = json.loads(raw.value)
            except ValueError as e:
                config_note = f"pubkit.json is not JSON ({e})"
        if not isinstance(config, dict):
            config = None
        kind = "service" if config and config.get("kind") == "service" else "package"
        facts["kind"] = Fact(kind, raw.sources, config_note)

        release_json = gh.release_json(repo)
        lock = gh.requirements_lock(repo)
        names = gh.workflow_names(repo)
        texts = [gh.workflow(repo, n) for n in names.value or [] if WORKFLOW_NAME.fullmatch(n)]
        facts["pins"], facts["integration"] = self.pins(repo, config, raw, release_json, lock,
                                                        names, texts)
        facts["latest"] = self.latest()

        live, live_note = self.live_block(config)
        facts["live"] = Fact(live, raw.sources, live_note)
        workflows = set(names.value or [])
        facts["released"] = self.released(repo, kind, live, workflows, names)
        facts["issues"] = gh.open_issues(repo)
        facts["dependabot_alerts"] = gh.dependabot_alerts(repo)
        facts["code_scanning_alerts"] = gh.code_scanning_alerts(repo)
        facts["secret_scanning_alerts"] = gh.secret_scanning_alerts(repo)
        facts["ci"] = self.ci(repo, branch)
        facts["nightly"] = self.nightly(repo, item, workflows, names)
        if kind == "service":
            facts["health"] = (self.c.live.health(live["health"]) if live.get("health")
                               else Fact(None, (), "no health URL declared"))
            facts["live_test"] = self.last_run(
                actions.live_test_runs(repo, live["test"]) if live.get("test") else
                Fact(None, (), "no live-test workflow declared"))
        facts["protection"] = self.protection(repo, item, config, raw, facts["visibility"])
        facts["sonar"] = self.c.sonarcloud.ratings(repo)
        if facts["visibility"].value == "public":
            facts["scorecard"] = self.c.scorecard.score(repo)
        return facts

    def pins(self, repo, config, raw, release_json, lock, names, texts) -> tuple[Fact, Fact]:
        """The pubkit and pubrel versions the repository pins, and whether
        it is integrated with each."""
        workflows = "\n".join(t.value for t in texts if t.value)
        pubkit = sorted(set(PIN.findall(lock.value or ""))
                        | {m["version"] for m in PUBKIT_PIN.finditer(workflows)})
        pubrel = {m["version"] for m in RELEASE_PIN.finditer(workflows)}
        pubrel |= {m["version"] for m in PUBREL_VERSION.finditer(workflows)}
        declared = ((config or {}).get("ci") or {}).get("release") if config else None
        if isinstance(declared, dict) and isinstance(declared.get("pubrel"), str):
            pubrel.add(declared["pubrel"].removeprefix("v"))
        pinned: dict[str, Any] = {"pubkit": pubkit, "pubrel": sorted(pubrel)}
        for tool, home in self.tools.items():
            if home == repo:
                pinned[tool] = "self"
        integrated = {
            "pubkit": bool(pinned["pubkit"]) and (config is not None or pinned["pubkit"] == "self"),
            "pubrel": bool(pinned["pubrel"]) and (release_json.value is not None
                                                  or pinned["pubrel"] == "self"),
        }
        read = merged(raw, lock, names, *texts, value=None)
        return (Fact(pinned, read.sources),
                Fact(integrated, merged(read, release_json, value=None).sources))

    @staticmethod
    def live_block(config: dict | None) -> tuple[dict, str | None]:
        """pubkit.json's live block, checked; empty, with why, when unsound."""
        block = (config or {}).get("live")
        if block is None:
            return {}, None
        if not isinstance(block, dict):
            return {}, "pubkit.json's live block is not an object"
        problems = [f"{k} is not a live setting" for k in sorted(block.keys() - LIVE_KEYS)]
        health = block.get("health")
        if health is not None and not (isinstance(health, str) and health.startswith("https://")):
            problems.append("health must be an https:// URL")
        for key in ("test", "deploy"):
            if key in block and not (isinstance(block[key], str)
                                     and WORKFLOW_NAME.fullmatch(block[key])):
                problems.append(f"{key} must be a workflow file name, such as live.yml")
        if problems:
            return {}, f"pubkit.json's live block: {'; '.join(problems)}"
        return dict(block), None

    def released(self, repo, kind, live, workflows, names) -> Fact:
        if kind != "service":
            return self.c.github.latest_release(repo)
        deploy = live.get("deploy") or self.rules.get("workflows", {}).get("deploy", "deploy.yml")
        if deploy not in workflows:
            return Fact(None, names.sources, f"no {deploy}")
        runs = self.c.actions.deploy_runs(repo, deploy)
        good = [r for r in runs.value or [] if r["conclusion"] == "success"]
        if runs.value is None:
            return runs
        if not good:
            return Fact(None, runs.sources, f"no successful {deploy} run")
        return Fact({"at": good[0]["at"], "url": good[0]["url"]}, runs.sources)

    @staticmethod
    def last_run(runs: Fact) -> Fact:
        if not runs.value:
            return Fact(None, runs.sources, runs.note or "no completed run")
        last = runs.value[0]
        return Fact({"conclusion": last["conclusion"], "at": last["at"], "url": last["url"]},
                    runs.sources)

    def ci(self, repo: str, branch: str) -> Fact:
        runs = self.c.actions.main_runs(repo, branch)
        if runs.value is None:
            return runs
        wanted = self.rules.get("workflows", {}).get("ci", ["ci.yml", "main.yml"])
        found = [r for r in runs.value if r["workflow"] in wanted]
        if not found:
            return Fact(None, runs.sources, f"no completed {' or '.join(wanted)} run on {branch}")
        last = found[0]
        jobs = self.c.actions.jobs(repo, last["id"])
        return merged(runs, jobs, value={**{k: last[k] for k in ("workflow", "conclusion", "at", "url")},
                                         "jobs": jobs.value or []}, note=jobs.note)

    def nightly(self, repo, item, workflows, names) -> Fact:
        workflow = self.rules.get("workflows", {}).get("nightly", "nightly.yml")
        if workflow not in workflows:
            return Fact({"governed": item["governed"], "last": None},
                        (*item["sources"], *names.sources))
        runs = self.c.actions.nightly_runs(repo, workflow)
        last = self.last_run(runs)
        return Fact({"governed": item["governed"], "last": last.value},
                    (*item["sources"], *runs.sources), runs.note)

    def protection(self, repo, item, config, raw, visibility) -> Fact:
        """How the repository's rulesets differ from what pubkit wants of it."""
        if item["entry"] is not None:
            wanted, why = item["entry"], item["sources"]
        else:
            try:
                wanted = protect.entry(repo, config, "its pubkit.json")
            except ValueError as e:
                return Fact({"differences": [f"not in protected.json, and {e}"], "limit": False},
                            raw.sources)
            why = list(raw.sources)
        found = self.c.github.rulesets(repo)
        sources = (*why, *found.sources)
        if found.value is None:
            if visibility.value == "private":
                return Fact({"differences": [found.note or "rulesets not readable"], "limit": True},
                            sources)
            return Fact(None, sources, found.note)
        differences = []
        targets = [("branch", protect.ruleset(wanted["checks"]), "its default branch")]
        if wanted.get("tags", True):
            targets.append(("tag", protect.tag_ruleset(), "its release tags"))
        for target, want, where in targets:
            test = protect.on_default_branch if target == "branch" else protect.on_release_tags
            have = [r for r in found.value if r.get("target") == target and test(r)]
            if not have:
                differences.append(f"no ruleset on {where}")
                continue
            named = [r for r in have if r.get("name") == want["name"]]
            chosen = (named or have)[0]
            differences += protect.differences(want, chosen)
            differences += [f"also on {where}: \"{r.get('name')}\"" for r in have if r is not chosen]
        return Fact({"differences": differences, "limit": False}, sources)
