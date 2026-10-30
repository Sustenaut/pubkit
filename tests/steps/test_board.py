"""Steps for `pubkit board`: the rules on plain fact files
(tests/fixtures/board/package.json and service.json), each connector
against the recorded responses of a made-up build
(tests/fixtures/board/recorded.json), and the command replaying it."""

from __future__ import annotations

import ast
import copy
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from pubkit.board import build
from pubkit.board import rules as grading
from pubkit.board.connectors.actions import Actions
from pubkit.board.connectors.base import Ledger, Recording, Replay
from pubkit.board.connectors.github import GitHub
from pubkit.board.connectors.live import Live
from pubkit.board.connectors.repos import Repos
from pubkit.board.connectors.scorecard import Scorecard
from pubkit.board.connectors.sonarcloud import SonarCloud

scenarios("board.feature")

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "board"
BOARD = ROOT / "src" / "pubkit" / "board"


# The rules, on fact files.

@pytest.fixture
def case():
    return {"rules": grading.load()}


def _load(case, name):
    data = json.loads((FIXTURES / name).read_text())
    case["facts"] = data["facts"]
    case["now"] = datetime.fromisoformat(data["now"].replace("Z", "+00:00"))


def _set(case, name, value, note=None):
    case["facts"][name] = {"value": value, **({"note": note} if note else {})}


@given("the facts of a package with nothing wrong")
def package(case):
    _load(case, "package.json")


@given("the facts of a service with nothing wrong")
def service(case):
    _load(case, "service.json")


@given(parsers.parse('Sonar rates reliability "{r}", maintainability "{m}" and security "{s}"'))
def sonar_rates(case, r, m, s):
    _set(case, "sonar", {"reliability": r, "maintainability": m, "security": s, "gate": "OK"})


@given("SonarQube Cloud has no analysis of it")
def no_sonar(case):
    _set(case, "sonar", None, "no SonarQube Cloud project")


@given(parsers.parse('main\'s last CI run has the failed jobs "{jobs}"'))
def failed_jobs(case, jobs):
    run = case["facts"]["ci"]["value"]
    names = [j.strip() for j in jobs.split(",")]
    known = {j["name"] for j in run["jobs"]}
    run["jobs"] = [{**j, "conclusion": "failure" if j["name"] in names else j["conclusion"]}
                   for j in run["jobs"]] + [{"name": n, "conclusion": "failure"}
                                            for n in names if n not in known]
    run["conclusion"] = "failure"


@given(parsers.parse("it has {count:d} open Dependabot alerts"))
def dependabot(case, count):
    _set(case, "dependabot_alerts", count)


@given(parsers.parse('its Dependabot alerts could not be read, "{note}"'))
def dependabot_unread(case, note):
    _set(case, "dependabot_alerts", None, note)


@given(parsers.parse("OpenSSF Scorecard scores it {score:g}"))
def scorecard(case, score):
    _set(case, "scorecard", {"score": score, "failing": ["Branch-Protection"]})


@given(parsers.parse('it pins pubkit "{pinned}" where the latest is "{latest}"'))
def behind(case, pinned, latest):
    case["facts"]["pins"]["value"]["pubkit"] = [pinned]
    case["facts"]["latest"]["value"]["pubkit"] = latest


@given("it is not in governed.json and has no nightly workflow")
def ungoverned(case):
    _set(case, "nightly", {"governed": False, "last": None})


@given(parsers.parse("it has {count:d} open issues"))
def open_issues(case, count):
    _set(case, "issues", count)


@given("it has no release.json, so is not integrated with pubrel")
def no_pubrel(case):
    case["facts"]["integration"]["value"]["pubrel"] = False


@given(parsers.parse('its last release was "{tag}" on "{at}"'))
def last_release(case, tag, at):
    _set(case, "released", {"tag": tag, "at": at})


@given("its live block declares no health URL")
def no_health(case):
    case["facts"]["live"]["value"].pop("health")
    case["facts"].pop("health")


@given(parsers.parse('its live test last passed at "{at}"'))
def live_test_at(case, at):
    _set(case, "live_test", {"conclusion": "success", "at": at})


@given(parsers.parse("its health URL answered {status:d}"))
def health_status(case, status):
    _set(case, "health", {"answers": False, "status": status})


@given("it is private and GitHub's plan gives it no rulesets")
def plan_limit(case):
    _set(case, "visibility", "private")
    case["facts"].pop("scorecard")
    _set(case, "protection", {"differences": [
        "Upgrade to GitHub Pro or make this repository public to enable this feature. (HTTP 403)"],
        "limit": True})


@given(parsers.parse('its protection differs: "{difference}"'))
def drift(case, difference):
    _set(case, "protection", {"differences": [difference], "limit": False})


@given(parsers.parse('rules without the "{notch}" notch'))
def without(case, notch):
    case["rules"]["delivery"] = [d for d in case["rules"]["delivery"] if d["notch"] != notch]


@given(parsers.parse('rules with a delivery notch "{notch}"'))
def odd_rules(case, tmp_path, notch):
    rules = copy.deepcopy(case["rules"])
    rules["delivery"].append({"notch": notch, "applies": "all"})
    case["path"] = tmp_path / "rules.json"
    case["path"].write_text(json.dumps(rules))


@then(parsers.parse('loading them fails with "{text}"'))
def refused(case, text):
    with pytest.raises(ValueError, match=text):
        grading.load(case["path"])


@when("I grade them", target_fixture="graded")
def grade(case):
    return grading.grade(case["facts"], case["rules"], case["now"])


def _grade(graded, name):
    return graded["grades"][name.lower()]


@then(parsers.parse('Quality is "{q}", Security is "{s}", Delivery is "{d}" and overall "{o}"'))
def letters(graded, q, s, d, o):
    got = tuple(_grade(graded, c)["letter"] for c in ("quality", "security", "delivery"))
    assert got == (q, s, d), graded
    assert graded["overall"] == o


@then(parsers.re(r'(?P<name>Quality|Security|Delivery) is "(?P<letter>[A-E])"$'))
def one_letter(graded, name, letter):
    assert _grade(graded, name)["letter"] == letter, _grade(graded, name)


@then(parsers.parse('the overall grade is "{letter}"'))
def overall(graded, letter):
    assert graded["overall"] == letter


@then("no grade lists a notch")
def no_notches(graded):
    for name in grading.CATEGORIES:
        assert _grade(graded, name)["steps"] == [], _grade(graded, name)


@then(parsers.parse('{name} starts at "{letter}" from "{text}"'))
def starts(graded, name, letter, text):
    start = _grade(graded, name)["start"]
    assert (start["letter"], start["text"]) == (letter, text)


def _texts(graded, name, delta):
    return [s["text"] for s in _grade(graded, name)["steps"] if s["delta"] == delta]


@then(parsers.parse('{name} lists the notch "{text}"'))
def lists_notch(graded, name, text):
    assert any(t.startswith(text) for t in _texts(graded, name, -1)), _texts(graded, name, -1)


@then(parsers.parse('{name} lists the note "{text}"'))
def lists_note(graded, name, text):
    assert text in _texts(graded, name, 0), _texts(graded, name, 0)


@then(parsers.parse("{name} lists, in order, {texts}"))
def lists_in_order(graded, name, texts):
    assert _texts(graded, name, -1) == re.findall(r'"([^"]*)"', texts)


@then(parsers.parse("{name} takes {count:d} notches"))
def takes(graded, name, count):
    grade = _grade(graded, name)
    assert grade["notches"] == count == len(_texts(graded, name, -1)), grade["steps"]


@given(parsers.parse("graded rows {rows}"), target_fixture="rows")
def rows(rows):
    found = re.findall(r'"([^"]+)" "([A-E])" with (\d+) notch', rows)
    return [{"repo": r, "overall": g, "notches": int(n)} for r, g, n in found]


@when("I order them", target_fixture="ordered")
def order(rows):
    return grading.order(rows)


@then(parsers.parse('the order is "{names}"'))
def the_order(ordered, names):
    assert [r["repo"] for r in ordered] == [n.strip() for n in names.split(",")]


# The connectors, against recorded responses.

@given("recorded responses from the build recording", target_fixture="sources")
def recording():
    ledger = Ledger(clock=lambda: datetime.fromisoformat("2026-10-27T12:00:00+00:00"))
    return {"ledger": ledger, "transport": Replay(FIXTURES / "recorded.json")}


def _connector(sources, cls):
    return cls(sources["ledger"], sources["transport"])


@when(parsers.parse('the repos connector reads "{protected}" and "{governed}"'),
      target_fixture="read")
def read_lists(sources, protected, governed):
    repos = _connector(sources, Repos)
    return {"protected": repos.protected(FIXTURES / protected),
            "governed": repos.governed(FIXTURES / governed)}


@then(parsers.parse('it lists "{names}"'))
def it_lists(read, names):
    listed = [e["repo"] for e in read["protected"].value]
    listed += [e["repo"] for e in read["governed"].value if e["repo"] not in listed]
    assert listed == [n.strip() for n in names.split(",")]


@then(parsers.parse('"{repo}" is governed'))
def is_governed(read, repo):
    assert repo in [e["repo"] for e in read["governed"].value]


@then("every call is recorded with its connector, method, endpoint and time")
def every_call(sources, read):
    calls = sources["ledger"].calls
    assert calls
    ids = {c["id"] for c in calls}
    for call in calls:
        assert {"connector", "method", "endpoint", "at"} <= call.keys(), call
        assert call["at"] == "2026-10-27T12:00:00Z"
    for fact in read.values():
        assert fact.sources and set(fact.sources) <= ids, fact


@when(parsers.parse('the github connector reads "{repo}"'), target_fixture="read")
def read_github(sources, repo):
    gh = _connector(sources, GitHub)
    return {"repository": gh.repository(repo), "pubkit_json": gh.pubkit_json(repo),
            "issues": gh.open_issues(repo), "dependabot": gh.dependabot_alerts(repo),
            "release": gh.latest_release(repo)}


@then(parsers.parse('its visibility is "{visibility}"'))
def visibility(read, visibility):
    assert read["repository"].value["visibility"] == visibility


@then(parsers.parse('its pubkit.json has the kind "{kind}"'))
def kind(read, kind):
    assert json.loads(read["pubkit_json"].value)["kind"] == kind


@then(parsers.parse("it has {count:d} open issue, the pull request it lists not counted"))
def issue_count(read, count):
    assert read["issues"].value == count


@then(parsers.parse('its Dependabot alerts could not be read: "{note}"'))
def unread(read, note):
    assert read["dependabot"].value is None and read["dependabot"].note == note


@then(parsers.parse('its latest release is "{tag}"'))
def latest(read, tag):
    assert read["release"].value["tag"] == tag


@when(parsers.parse('the actions connector reads main\'s runs of "{repo}"'), target_fixture="read")
def read_actions(sources, repo):
    actions = _connector(sources, Actions)
    runs = actions.main_runs(repo)
    return {"runs": runs, "jobs": actions.jobs(repo, runs.value[0]["id"])}


@then(parsers.parse('the newest run is "{workflow}", "{conclusion}"'))
def newest(read, workflow, conclusion):
    run = read["runs"].value[0]
    assert (run["workflow"], run["conclusion"]) == (workflow, conclusion)


@then(parsers.parse('its jobs include "{name}" "{conclusion}"'))
def job(read, name, conclusion):
    assert {"name": name, "conclusion": conclusion} in read["jobs"].value


@when(parsers.parse('the sonarcloud connector reads "{a}" and "{b}"'), target_fixture="read")
def read_sonar(sources, a, b):
    sonar = _connector(sources, SonarCloud)
    return {a: sonar.ratings(a), b: sonar.ratings(b)}


@then(parsers.parse('"{repo}" rates reliability "{r}", maintainability "{m}" and security "{s}"'))
def rates(read, repo, r, m, s):
    assert read[repo].value == {"reliability": r, "maintainability": m, "security": s, "gate": "OK"}


@then(parsers.parse('"{repo}" has no ratings, noted "{note}"'))
def no_ratings(read, repo, note):
    assert read[repo].value is None and read[repo].note == note


@when(parsers.parse('the scorecard connector reads "{a}" and "{b}"'), target_fixture="read")
def read_scorecard(sources, a, b):
    card = _connector(sources, Scorecard)
    return {a: card.score(a), b: card.score(b)}


@then(parsers.parse('"{repo}" scores {score:g}'))
def scores(read, repo, score):
    assert read[repo].value["score"] == score
    assert read[repo].value["failing"] == ["Branch-Protection"]


@then(parsers.parse('"{repo}" is not scored'))
def not_scored(read, repo):
    assert read[repo].value is None


@when(parsers.parse('the live connector reads "{a}" and "{b}"'), target_fixture="read")
def read_live(sources, a, b):
    live = _connector(sources, Live)
    return {"first": live.health(a), "second": live.health(b)}


@then("the first answers 200 and the second does not answer")
def answers(read):
    assert read["first"].value == {"answers": True, "status": 200}
    assert read["second"].value == {"answers": False, "status": None}
    assert read["second"].note.startswith("no answer")


# The command, replaying the recorded build.

@given("the build recording, protected.json and governed.json in the repository")
def recorded_build(repo):
    for name in ("recorded.json", "protected.json", "governed.json"):
        shutil.copy(FIXTURES / name, repo / name)


def _board(repo, path):
    return json.loads((repo / path).read_text())


@then(parsers.parse('"{path}" lists the repositories worst first'))
def worst_first(repo, path):
    board = _board(repo, path)
    assert [r["repo"] for r in board["repos"]] == [
        "example-org/private-thing", "example-org/site-thing", "example-org/pubthing"]
    assert [r["overall"] for r in board["repos"]] == ["E", "C", "B"]


@then(parsers.parse('every fact and notch in "{path}" names calls that board.json records'))
def linked(repo, path):
    board = _board(repo, path)
    ids = {c["id"] for c in board["calls"]}
    assert not [c for c in board["calls"] if c.get("error") == "not recorded"]
    for row in board["repos"]:
        for name, fact in row["facts"].items():
            assert set(fact["sources"]) <= ids, (row["repo"], name)
        for grade in row["grades"].values():
            for step in grade["steps"]:
                assert step["sources"] and set(step["sources"]) <= ids, (row["repo"], step)


@then(parsers.parse('"{path}" shows "{name}" as private, with its grades'))
def private_row(repo, path, name):
    html = (repo / path).read_text()
    row = next(line for line in html.splitlines() if name in line)  # one line per row
    assert "<span class=tag>private</span>" in row
    assert row.count("class=grade") == 3


@then(parsers.parse('"{path}" has a breakdown naming "{label}"'))
def breakdown(repo, path, label):
    assert label in (repo / path).read_text()


@then(parsers.parse('"{path}" loads no script'))
def no_script(repo, path):
    html = (repo / path).read_text().lower()
    assert "<script" not in html and "src=" not in html


@when("I build the board while recording it, then build it again from that recording",
      target_fixture="boards")
def record_then_replay(sources, tmp_path, monkeypatch):
    monkeypatch.chdir(FIXTURES)
    when_built = sources["transport"].recorded_at
    recording = Recording(sources["transport"])
    first = build(Path("protected.json"), Path("governed.json"), grading.load(), recording,
                  when_built)
    recording.save(tmp_path / "again.json", when_built)
    second = build(Path("protected.json"), Path("governed.json"), grading.load(),
                   Replay(tmp_path / "again.json"), when_built)
    return first, second


@then("the two boards grade every repository the same, from the same calls")
def same_boards(boards):
    first, second = boards
    assert [(r["repo"], r["grades"]) for r in first["repos"]] == \
        [(r["repo"], r["grades"]) for r in second["repos"]]
    assert [c["endpoint"] for c in first["calls"]] == [c["endpoint"] for c in second["calls"]]


# Where the network is reached.

@when("I read the board's modules outside its connectors", target_fixture="modules")
def modules():
    return [p for p in BOARD.rglob("*.py") if "connectors" not in p.relative_to(BOARD).parts]


@then("none imports urllib, http, socket, ssl or subprocess")
def no_network(modules):
    assert modules
    banned = {"urllib", "http", "socket", "ssl", "subprocess"}
    for path in modules:
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            for name in names:
                assert name.split(".")[0] not in banned, f"{path.name} imports {name}"
