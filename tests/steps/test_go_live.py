"""Steps for go-live: the caller `pubrules sync` writes, and `pubrules go-live`'s
poll against a fake engine, a fake clock and a fake GitHub token service."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pytest_bdd import given, parsers, scenarios, then, when

from pubrules import golive

scenarios("go-live.feature")

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "eyJ-a-run-token"


def _config(repo: Path) -> dict:
    return json.loads((repo / "pubrules.json").read_text())


def _save(repo: Path, config: dict) -> None:
    (repo / "pubrules.json").write_text(json.dumps(config, indent=2) + "\n")


@given(parsers.parse('pubrules.json has a deploy block for "{namespace}" and the unit "{unit}"'))
def deploy_block(repo, namespace, unit):
    config = _config(repo)
    config["deploy"] = {"namespace": namespace, "units": {unit: {"namespace": f"{namespace}-{unit}"}}}
    _save(repo, config)


@given("pubrules.json has no deploy block")
def no_deploy_block(repo):
    config = _config(repo)
    config.pop("deploy", None)
    _save(repo, config)


@given(parsers.re(r"pubrules\.json's go-live block is (?P<block>.+)"))
def go_live_block(repo, block):
    config = _config(repo)
    config["go-live"] = json.loads(block)
    _save(repo, config)


@given(parsers.parse('pubrules.json\'s ci block runs on "{label}"'))
def runs_on(repo, label):
    config = _config(repo)
    config.setdefault("ci", {})["runs-on"] = label
    _save(repo, config)


@given(parsers.re(r'I run pubrules (?P<args>(?:"[^"]*" ?)+)'))
def ran_pubrules(repo, run, env, args):
    from conftest import run_pubrules  # noqa: PLC0415
    assert run_pubrules(repo, run, env, args).code == 0


@then(parsers.parse('"{path}" lists "{listed}" under "{plugin}"'))
def lists_under(repo, path, listed, plugin):
    uses = json.loads((repo / path).read_text())["uses"]
    assert listed in next(u for u in uses if u["plugin"] == plugin)["paths"]


@then("the caller and integrations.json are in step")
def in_step(result):
    # The deploy block itself is pubdeploy's to check, which this suite does not install.
    assert "go-live.yml" not in result.stderr and "integrations.json" not in result.stderr, result.stderr


@then(parsers.parse('the repository has no "{path}"'))
def has_no(repo, path):
    assert not (repo / path).exists()


@then("pubrules' go-live.yml takes the unit, asks for id-token write, and runs pubrules go-live")
def reusable():
    flow = yaml.safe_load((ROOT / ".github/workflows/go-live.yml").read_text())
    inputs = flow[True]["workflow_call"]["inputs"]  # YAML reads `on` as true
    assert inputs["unit"]["required"] is True and inputs["kind"]["default"] == "fulfil"
    assert inputs["timeout-minutes"]["default"] == 300
    job = flow["jobs"]["go-live"]
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    assert "secrets" not in flow[True]["workflow_call"]
    run = job["steps"][-1]["run"]
    assert "pubrules go-live --unit \"$UNIT\"" in run and "${{" not in run
    from pubrules.refs import SHIPPED  # noqa: PLC0415
    assert "go-live.yml" in SHIPPED["workflows"]


# --- the poll ---------------------------------------------------------------------


class Engine:
    """The engine's answers, in turn: a state, a status, no answer, or garbage. The
    last is given again once they run out."""

    def __init__(self) -> None:
        self.answers: list = []
        self.asked: list[tuple[str, str, dict, bytes | None]] = []
        self.refusal: tuple[int, str] | None = None

    def send(self, method, url, headers, data):
        self.asked.append((method, url, headers, data))
        assert headers["Authorization"] == f"Bearer {TOKEN}"
        if method == "POST" and self.refusal:
            return self.refusal[0], json.dumps({"refused": self.refusal[1]}).encode()
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if answer == "no answer":
            raise ConnectionRefusedError("refused")
        if answer == "garbage":
            return 200, b"<html>"
        if isinstance(answer, int):
            return answer, b"{}"
        if isinstance(answer, tuple):
            return answer
        return 200 if method == "GET" else 201, json.dumps(answer).encode()


def request(state: str, needs: dict | None = None, error: str = "") -> dict:
    return {"id": "20261117T100000Z-ab12", "kind": "fulfil", "state": state, "unit": "example",
            "url": "https://convergence.example/approvals/20261117T100000Z-ab12",
            "result": {"needs": [{"need": k, "state": v} for k, v in (needs or {}).items()], "error": error}}


class Run:
    def __init__(self) -> None:
        self.engine = Engine()
        self.now = 0.0
        self.waits: list[float] = []
        self.lines: list[str] = []
        self.summary: list[str] = []
        self.code: int | None = None

    def http(self) -> golive.Http:
        def sleep(seconds):
            self.waits.append(seconds)
            self.now += seconds
        return golive.Http(send=self.engine.send, token=lambda audience: TOKEN, sleep=sleep,
                           clock=lambda: self.now, out=self.lines.append, summary=self.summary.append)


@pytest.fixture
def poll():
    return Run()


def _answer(text: str):
    """One answer as the feature names it: "state", "state" with "need" ready,
    "failed" saying "why", a status, no answer, or garbage."""
    text = text.strip()
    if text == "no answer" or text == "garbage":
        return text
    if text.isdigit():
        return int(text)
    if " with " in text:
        state, need = text.split(" with ")
        name, ready = need.rsplit(" ", 1)
        return request(state.strip('"'), {name.strip('"'): ready})
    if " saying " in text:
        state, why = text.split(" saying ")
        return request(state.strip('"'), error=why.strip('"'))
    return request(text.strip('"'))


@given(parsers.re(r"the engine answers the request with (?!.*refuses)(?P<answers>.+?)(?P<every> every time)?$"))
def engine_answers(poll, answers, every):
    poll.engine.answers = [_answer(a) for a in answers.split(", then ")]


@given("the engine never answers")
def never(poll):
    poll.engine.answers = ["no answer"]


@given(parsers.parse('the engine refuses the run with {status:d} "{reason}"'))
def refuses(poll, status, reason):
    poll.engine.refusal = (status, reason)


@given(parsers.parse('the engine answers the request with "{state}", then refuses with {status:d} "{reason}"'))
def refuses_later(poll, state, status, reason):
    poll.engine.answers = [request(state), (status, json.dumps({"refused": reason}).encode())]


@when(parsers.parse('the go-live of "{unit}" runs for {minutes:d} minutes'))
def runs(poll, unit, minutes):
    poll.code = golive.go_live(poll.http(), "https://convergence.example/", unit, "fulfil", minutes)


@when(parsers.parse('the gate "{unit}" with the plan "{plan}" runs for {minutes:d} minutes'))
def gate_runs(poll, unit, plan, minutes):
    poll.code = golive.go_live(poll.http(), "https://convergence.example", unit, "gate", minutes, plan)


@then(parsers.parse("it exits {code:d}"))
def exits(poll, code):
    assert poll.code == code, (poll.lines, poll.summary)


@then(parsers.parse("it printed each state once: {states}"))
def each_once(poll, states):
    said = [line for line in poll.lines if not line.startswith("go-live: asked")]
    wanted = [golive.SAID[s] for s in states.split(", ")]
    for state in wanted:
        assert sum(line.endswith(state) or f" {state}" in line for line in said) >= 1, (state, said)
    assert sum(golive.SAID["awaiting"] in line for line in said) == 1, said


@then("the job summary links the approval page")
def links_page(poll):
    assert any("Awaiting approval: [https://convergence.example/approvals/" in s for s in poll.summary)


@then(parsers.parse('the job summary says "{text}"'))
def summary_says(poll, text):
    assert any(text in s for s in poll.summary), poll.summary


@then("nothing it printed or summarised holds an email or the token")
def no_email(poll):
    text = "\n".join(poll.lines + poll.summary)
    assert "@" not in text and TOKEN not in text, text


@then(parsers.parse("it waited {waits}"))
def waited(poll, waits):
    assert poll.waits == [float(w) for w in waits.removesuffix(" seconds").split(", ")], poll.waits


@then(parsers.parse('it printed "{text}"'))
def printed(poll, text):
    assert any(text in line for line in poll.lines), poll.lines


@then(parsers.parse('the engine was asked for a gate with the plan "{plan}"'))
def asked_gate(poll, plan):
    method, url, _, data = poll.engine.asked[0]
    assert (method, url) == ("POST", "https://convergence.example/requests")
    assert json.loads(data) == {"kind": "gate", "unit": "release", "plan": plan}


# --- GitHub's token service --------------------------------------------------------


class Tokens:
    def __init__(self) -> None:
        self.asked: list[str] = []
        self.status = 200

    def get(self, method, url, headers, data):
        self.asked.append(url)
        assert headers["Authorization"] == "Bearer the-request-token" and method == "GET"
        return self.status, json.dumps({"value": f"{TOKEN}-{len(self.asked)}"}).encode()


@pytest.fixture
def github():
    return {"tokens": Tokens(), "env": {"ACTIONS_ID_TOKEN_REQUEST_URL": "https://token.example/x?api-version=2",
                                        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "the-request-token"}, "out": []}


@given("GitHub Actions gives OIDC tokens")
def gives(github):
    pass


@given("GitHub Actions gives no OIDC request address")
def gives_none(github):
    github["env"].pop("ACTIONS_ID_TOKEN_REQUEST_URL")


@given("GitHub Actions answers 500 for the token")
def gives_500(github):
    github["tokens"].status = 500


@when(parsers.parse('the go-live asks for two tokens for "{audience}"'))
def two_tokens(github, audience):
    token = golive.actions_token(github["env"], github["tokens"].get, github["out"].append)
    github["got"] = [token(audience), token(audience)]


@then(parsers.parse('GitHub was asked twice with the audience "{quoted}"'))
def asked_twice(github, quoted):
    assert github["tokens"].asked == [f"https://token.example/x?api-version=2&audience={quoted}"] * 2
    assert github["got"] == [f"{TOKEN}-1", f"{TOKEN}-2"]


@then("each token was masked in the log")
def masked(github):
    assert github["out"] == [f"::add-mask::{TOKEN}-1", f"::add-mask::{TOKEN}-2"]


@then("asking for a token stops, saying the job needs id-token: write")
def stops(github):
    with pytest.raises(SystemExit, match="id-token: write"):
        golive.actions_token(github["env"], github["tokens"].get, github["out"].append)


@then("asking for a token is no answer")
def no_token(github):
    token = golive.actions_token(github["env"], github["tokens"].get, github["out"].append)
    with pytest.raises(golive.Unanswered):
        token("https://engine.example.eu")
