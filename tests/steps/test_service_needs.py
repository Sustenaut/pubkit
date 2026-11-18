"""Steps for service-needs: the caller `pubrules sync` writes, and `pubrules service-needs`'s
poll against a fake engine, a fake clock and a fake GitHub token service."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from pytest_bdd import given, parsers, scenarios, then, when

from pubrules import service_needs as golive

scenarios("service-needs.feature")

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


@given(parsers.re(r"pubrules\.json's service-needs block is (?P<block>.+)"))
def service_needs_block(repo, block):
    config = _config(repo)
    config["service-needs"] = json.loads(block)
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
    assert "service-needs.yml" not in result.stderr and "integrations.json" not in result.stderr, result.stderr


@then("the caller, integrations.json and the review's list are in step")
def all_in_step(result):
    # The deploy block itself is pubdeploy's to check, which this suite does not install.
    assert not any(name in result.stderr for name in ("service-needs.yml", "go-live.yml", "integrations.json",
                                                       "dependency-review-licenses.txt")), result.stderr


@then("pubrules' service-needs.yml takes the unit, asks for id-token write, and runs pubrules service-needs")
def reusable(shipped):
    flow = yaml.safe_load(shipped["workflows"]["service-needs.yml"])
    inputs = flow[True]["workflow_call"]["inputs"]  # YAML reads `on` as true
    assert inputs["unit"]["required"] is True and inputs["kind"]["default"] == "check"
    assert inputs["timeout-minutes"]["default"] == 300
    job = flow["jobs"]["service-needs"]
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    assert "secrets" not in flow[True]["workflow_call"]
    asked, waited = job["steps"][-2], job["steps"][-1]
    assert asked["id"] == "ask" and "pubrules service-needs --stage ask --unit \"$UNIT\"" in asked["run"]
    assert "--stage wait --request \"$REQUEST\"" in waited["run"]
    assert waited["env"]["REQUEST"] == "${{ steps.ask.outputs.request }}"
    assert waited["env"]["MINUTES"] == "${{ steps.ask.outputs.minutes }}"
    assert "${{" not in asked["run"] + waited["run"]
    from pubrules.refs import SHIPPED  # noqa: PLC0415
    assert "service-needs.yml" in SHIPPED["workflows"]


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


def request(state: str, needs: dict | None = None, error: str = "", note: str = "",
            found: dict | None = None) -> dict:
    """An answer of the engine's: every need with its type, grade, state and what was found,
    and the plan (k8s.secret:s missing, which the fulfil makes)."""
    found = found or {}
    listed = [{"need": k, "type": k.split(":")[0], "grade": "auto", "state": v, "detail": found.get(k, f"{k} is {v}")}
              for k, v in (needs or {}).items()]
    return {"id": "20261117T100000Z-ab12", "kind": "fulfil", "state": state, "unit": "example",
            "url": "https://convergence.example/approvals/20261117T100000Z-ab12", "expires_at": "2026-11-18T10:00:00Z",
            "plan": [{"need": "k8s.secret:s", "state": "missing",
                      "change": "the fulfil makes it: k8s.secret:s is missing"}],
            "result": {"needs": listed, "error": error, "note": note}}


class Run:
    def __init__(self) -> None:
        self.engine = Engine()
        self.now = 0.0
        self.waits: list[float] = []
        self.lines: list[str] = []
        self.summary: list[str] = []
        self.code: int | None = None
        self.outputs: list[tuple[str, str]] = []
        self.asked = None

    def http(self) -> golive.Http:
        def sleep(seconds):
            self.waits.append(seconds)
            self.now += seconds
        return golive.Http(send=self.engine.send, token=lambda audience: TOKEN, sleep=sleep,
                           clock=lambda: self.now, out=self.lines.append, summary=self.summary.append,
                           output=lambda name, value: self.outputs.append((name, value)))


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
    if " with needs " in text:
        state, rest = text.split(" with needs ")
        note = re.search(r' noted "([^"]+)"', rest)
        found = re.search(r'"([^"]+)" (\w+) found "([^"]+)"', rest)
        needs = dict(re.findall(r'"([^"]+)" (\w+)', re.sub(r' (noted|found) "[^"]+"', "", rest)))
        return request(state.strip('"'), needs, note=note[1] if note else "",
                       found={found[1]: found[3]} if found else None)
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


@when(parsers.parse('the fulfil of "{unit}" runs for {minutes:d} minutes'))
def runs(poll, unit, minutes):
    poll.code = golive.service_needs(poll.http(), "https://convergence.example/", unit, "fulfil", minutes)


@when(parsers.parse('the gate "{unit}" with the plan "{plan}" runs for {minutes:d} minutes'))
def gate_runs(poll, unit, plan, minutes):
    poll.code = golive.service_needs(poll.http(), "https://convergence.example", unit, "gate", minutes, plan)


@then(parsers.parse("it exits {code:d}"))
def exits(poll, code):
    assert poll.code == code, (poll.lines, poll.summary)


@then(parsers.parse("it printed each state once: {states}"))
def each_once(poll, states):
    said = [line for line in poll.lines if not line.startswith("service-needs: asked")]
    wanted = [golive.SAID[s] for s in states.split(", ")]
    for state in wanted:
        assert sum(line.endswith(state) or f" {state}" in line for line in said) >= 1, (state, said)
    assert sum(golive.SAID["awaiting"] in line for line in said) == 1, said


@then("the job summary links the approval page")
def links_page(poll):
    assert any("**Approval needed.**" in s and "(https://convergence.example/approvals/20261117T100000Z-ab12)" in s
               for s in poll.summary), poll.summary


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


@when(parsers.parse('the service-needs asks for two tokens for "{audience}"'))
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


@when(parsers.parse('the service-needs asks for a token for "{audience}"'))
def one_token(pubtest_ctx, github, audience):
    pubtest_ctx.attempt(lambda: golive.actions_token(github["env"], github["tokens"].get, github["out"].append)(audience))


@then(parsers.parse("GitHub was asked {times:d} times"))
def asked(github, times):
    assert len(github["tokens"].asked) == times, github["tokens"].asked


@given(parsers.re(r'the pubrules\.json key "(?P<key>[^"]+)" is (?P<value>.+)'))
def config_key(repo, key, value):
    config = _config(repo)
    config[key] = json.loads(value)
    _save(repo, config)


@when(parsers.parse('the check of "{unit}" runs for {minutes:d} minutes'))
def check_runs(poll, unit, minutes):
    poll.code = golive.service_needs(poll.http(), "https://convergence.example", unit, "check", minutes)


@then(parsers.parse('the log has the group "{title}" holding "{line}"'))
def has_group(poll, title, line):
    at = poll.lines.index(f"::group::{title}")
    end = poll.lines.index("::endgroup::", at)
    assert line in poll.lines[at + 1:end], poll.lines[at:end + 1]


@then(parsers.parse('the job summary has the row "{row}"'))
def has_row(poll, row):
    assert any(row in s for s in poll.summary), poll.summary


@then(parsers.parse('the log has the annotation "{line}"'))
def has_annotation(poll, line):
    assert line in poll.lines, poll.lines


@then("it asked for nobody's approval")
def no_approval(poll):
    assert not any("Approval needed" in s for s in poll.summary + poll.lines)


@when(parsers.parse('the ask step for "{unit}" runs'))
def ask_step(poll, unit):
    poll.asked = golive.ask(poll.http(), "https://convergence.example", unit, "fulfil", 300 * 60)


@when(parsers.parse('the ask step of a check of "{unit}" runs'))
def ask_check_step(poll, unit):
    poll.asked = golive.ask(poll.http(), "https://convergence.example", unit, "check", 300 * 60)


@then(parsers.parse('the job summary has the approval box, linking the request and naming "{need}"'))
def approval_box(poll, need):
    box = next(s for s in poll.summary if "**Approval needed.**" in s)
    assert box.startswith("> [!IMPORTANT]") and "(https://convergence.example/approvals/20261117T100000Z-ab12)" in box
    assert f"`{need}` (missing): the fulfil makes it" in box
    assert "> It expires 2026-11-18 10:00 UTC, undecided." in box


@then(parsers.parse('the step output "{name}" is "{value}"'))
def step_output(poll, name, value):
    assert (name, value) in poll.outputs, poll.outputs


@then("the ask step ends without waiting for the decision")
def ask_ends(poll):
    assert isinstance(poll.asked, dict) and poll.asked["state"] == "awaiting"


@then("the job summary says nothing of approval")
def nothing_of_approval(poll):
    assert not any("pproval" in s for s in poll.summary), poll.summary


@when(parsers.parse('the wait step for the request "{request}" of "{unit}" runs for {minutes:d} minutes'))
def wait_step(poll, request, unit, minutes):
    poll.code = golive.wait(poll.http(), "https://convergence.example", unit, "fulfil", minutes, request)


@then(parsers.parse('the engine was polled for "{path}"'))
def polled(poll, path):
    assert ("GET", f"https://convergence.example{path}") in [(m, u) for m, u, _, _ in poll.engine.asked]


@given(parsers.parse('the engine refuses the poll with {status:d} "{reason}"'))
def refuses_poll(poll, status, reason):
    poll.engine.answers = [(status, json.dumps({"refused": reason}).encode())]


@given("the go-live workflow pubrules 0.43.2 wrote is there")
def old_caller(repo):
    target = repo / golive.OLD_CALLER
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(golive.HEADER + "name: go-live\n")


@given("the go-live workflow pubrules 0.43.2 wrote is there, calling pubrules' go-live.yml")
def old_caller_calling(repo):
    target = repo / golive.OLD_CALLER
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(golive.HEADER + "name: go-live\njobs:\n  go-live:\n"
                      "    uses: Sustenaut/pubrules/.github/workflows/go-live.yml@0123456789abcdef0123456789abcdef01234567\n")


@then("the ask step fails")
def ask_fails(poll):
    assert poll.asked == 1


@then(parsers.parse('pubrules.json has the key "{key}" as {value}, and no "{old}"'))
def renamed(repo, key, value, old):
    config = _config(repo)
    assert config[key] == json.loads(value) and old not in config
