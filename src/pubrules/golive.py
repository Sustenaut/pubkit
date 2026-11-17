"""Go-live from GitHub Actions: a workflow run asks the convergence engine to do a
step, a person approves it on the engine's page, and the run waits for the end.

    pubrules go-live --unit UNIT [--kind fulfil|gate] [--plan TEXT] [--minutes 300] [--engine URL]

The run's identity is its GitHub Actions OIDC token, for the engine's own address
as audience: the job needs `id-token: write`, and nothing else, no secret. The
token is asked for afresh for every call, since a wait outlives one; it is masked
in the log and never printed. The engine (Sustenaut/convergence-engine) takes

    POST ENGINE/requests        {"kind": "fulfil", "unit": UNIT}
    GET  ENGINE/requests/ID     the request's state, for the run that asked

and the run polls every 15 seconds, waiting longer after an answer of 5xx or no
answer at all, until the request ends or the time is up:

- done (or a gate approved): success, each need's end state in the job summary;
- declined, expired or failed (a plan that changed since its approval among them):
  failure, with the engine's reason;
- still awaiting, or still running, when the time is up: success, saying so: the
  engine runs it when a person approves it, whether or not the run still waits.

Each state is printed once, the approval page's link goes to the job summary while
the request awaits a decision, and no email (the engine names none to a caller) or
other value is ever printed: what the engine says is printed with any email-shaped
text taken out.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

ENGINE = "https://convergence.sustenaut.eu"
EVERY = 15.0  # seconds between polls
LONGEST_WAIT = 120.0  # the most a poll waits after a failed call
KINDS = ("fulfil", "gate")
ENDED = {"done": 0, "declined": 1, "expired": 1, "failed": 1}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SAID = {"planning": "the engine is walking the unit read-only for its plan",
        "awaiting": "awaiting a person's approval",
        "approved": "approved",
        "running": "the engine is fulfilling the unit",
        "done": "done",
        "declined": "declined",
        "expired": "expired: nobody decided in time",
        "failed": "failed"}


CALLER = ".github/workflows/go-live.yml"
HEADER = ("# Written by `pubrules sync` from pubrules.json's deploy block. Do not edit:\n"
          "# change pubrules.json and run `pubrules sync`.\n")
BLOCK = "go-live"
BLOCK_KEYS = ("engine", "timeout-minutes", "claude-dispatch")


def block_problems(block: Any) -> list[str]:
    """What is wrong with pubrules.json's go-live block, each a phrase."""
    if not isinstance(block, dict):
        return ['it must be an object, such as {"claude-dispatch": false}']
    out = [f"{key} is not a setting; it takes {', '.join(BLOCK_KEYS)}" for key in sorted(set(block) - set(BLOCK_KEYS))]
    if "engine" in block and not (isinstance(block["engine"], str)
                                  and re.fullmatch(r"https://[A-Za-z0-9.-]+(:[0-9]+)?", block["engine"])):
        out.append("engine must be the convergence engine's https:// address, with no path")
    if "timeout-minutes" in block and not (isinstance(block["timeout-minutes"], int)
                                           and not isinstance(block["timeout-minutes"], bool)
                                           and 1 <= block["timeout-minutes"] <= 340):
        out.append("timeout-minutes must be a whole number of minutes, 1 to 340")
    if "claude-dispatch" in block and not isinstance(block["claude-dispatch"], bool):
        out.append("claude-dispatch must be true or false")
    return out


def is_caller(text: str) -> bool:
    return text.startswith(HEADER)


def units(deploy: dict[str, Any]) -> list[str]:
    """The deploy block's units, as pubdeploy names them: the top level's (its
    namespace) first, then `units`, in name order."""
    return [deploy["namespace"], *sorted(deploy.get("units") or {})]


def caller(config: dict[str, Any], owner: str, pin: str) -> str | None:
    """The go-live workflow sync writes in a repository with a deploy block, or None
    without one: run by hand, it calls the reusable go-live.yml at this pubrules's
    release for the unit chosen. `pin` is what follows @, the release's commit and
    version."""
    deploy = config.get("deploy")
    if not isinstance(deploy, dict) or not isinstance(deploy.get("namespace"), str):
        return None
    given = config.get(BLOCK) or {}
    ci = config.get("ci") or {}
    runs_on = ci.get("runs-on")
    label = runs_on if isinstance(runs_on, str) else (runs_on or {}).get("default")
    inputs = {"unit": "${{ inputs.unit }}"}
    if "engine" in given:
        inputs["engine"] = given["engine"]
    if "timeout-minutes" in given:
        inputs["timeout-minutes"] = given["timeout-minutes"]
    if label:
        inputs["runs-on"] = label
    options = ", ".join(json.dumps(u) for u in units(deploy))
    dispatch = ("# go-live.claude-dispatch is true: Claude may start this workflow too.\n"
                if given.get("claude-dispatch") else "")
    return (HEADER + dispatch
            + "name: go-live\n\n"
            "on:\n  workflow_dispatch:\n    inputs:\n      unit:\n"
            "        description: The deploy unit the convergence engine fulfils, once a person approves its plan\n"
            f"        type: choice\n        required: true\n        options: [{options}]\n\n"
            "permissions:\n  contents: read\n\n"
            "jobs:\n  go-live:\n"
            f"    uses: {owner}/pubrules/.github/workflows/go-live.yml@{pin}\n"
            f"    with: {json.dumps(inputs)}\n"
            "    permissions:\n      contents: read\n      id-token: write\n")


class Unanswered(Exception):
    """The engine did not answer, or answered 5xx: worth asking again."""


class Refused(Exception):
    """The engine refused the run (4xx): asking again would not help."""


@dataclass
class Http:
    """The calls the poll makes, so a test can give fakes for each."""
    send: Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]
    token: Callable[[str], str]
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    out: Callable[[str], None] = print
    summary: Callable[[str], None] = lambda _text: None


@dataclass
class Poll:
    """What the run saw: each state once, and the last answer."""
    states: list[str] = field(default_factory=list)
    answer: dict[str, Any] = field(default_factory=dict)


def clean(text: Any) -> str:
    """The engine's words, as a log line may hold them: no email, one line."""
    return EMAIL.sub("[someone]", " ".join(str(text).split()))[:300]


def call(http: Http, engine: str, method: str, path: str, body: dict | None = None) -> dict[str, Any]:
    """One call to the engine, as the run: its JSON answer, or Unanswered or Refused."""
    headers = {"Authorization": f"Bearer {http.token(engine)}", "Accept": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    try:
        status, raw = http.send(method, engine + path, headers, data)
    except OSError as e:
        raise Unanswered(f"no answer ({type(e).__name__})") from None
    if status >= 500:
        raise Unanswered(f"the engine answered {status}")
    try:
        answer = json.loads(raw or b"{}")
    except ValueError:
        answer = {}
    if status >= 400:
        reason = answer.get("refused") if isinstance(answer, dict) else None
        raise Refused(f"the engine refused the request ({status}{', ' + clean(reason) if reason else ''})")
    if not isinstance(answer, dict) or not isinstance(answer.get("state"), str):
        raise Unanswered("the engine's answer is not a request")
    return answer


def retried(http: Http, deadline: float, what: Callable[[], dict[str, Any]]) -> dict[str, Any] | None:
    """`what`, asked again after Unanswered with a wait that doubles from EVERY up to
    LONGEST_WAIT, until it answers or the deadline passes (None then)."""
    wait = EVERY
    while True:
        try:
            return what()
        except Unanswered as e:
            if http.clock() + wait > deadline:
                http.out(f"go-live: {e}; the time is up")
                return None
            http.out(f"go-live: {e}; asking again in {wait:g} s")
            http.sleep(wait)
            wait = min(wait * 2, LONGEST_WAIT)


def needs_table(answer: dict[str, Any]) -> str:
    needs = (answer.get("result") or {}).get("needs") or []
    if not needs:
        return ""
    rows = "".join(f"| `{clean(n.get('need', ''))}` | {clean(n.get('state', ''))} |\n" for n in needs)
    return "\n| Need | State |\n| --- | --- |\n" + rows


def go_live(http: Http, engine: str, unit: str, kind: str, minutes: float, plan: str = "") -> int:
    """Ask, then poll until the request ends or the time is up: the exit code."""
    engine = engine.rstrip("/")
    deadline = http.clock() + minutes * 60
    poll = Poll()
    try:
        body = {"kind": kind, "unit": unit, **({"plan": plan or f"go-live of {unit}"} if kind == "gate" else {})}
        first = retried(http, deadline, lambda: call(http, engine, "POST", "/requests", body))
    except Refused as e:
        http.out(f"go-live: {e}")
        http.summary(f"### Go-live of {clean(unit)}\n\nNot asked: {e}.\n")
        return 1
    if first is None:
        http.summary(f"### Go-live of {clean(unit)}\n\nThe engine did not answer: nothing was asked.\n")
        return 1
    request = clean(first.get("id", ""))
    page = clean(first.get("url", ""))
    http.out(f"go-live: asked the engine to {kind} {unit}: request {request}")
    answer, linked = first, False
    while True:
        poll.answer = answer
        state = answer["state"]
        if state not in poll.states:
            poll.states.append(state)
            http.out(f"go-live: {SAID.get(state, clean(state))}")
            if state == "awaiting" and not linked:
                linked = True
                http.summary(f"### Go-live of {clean(unit)}\n\nAwaiting approval: [{page}]({page})\n")
        if state in ENDED or (kind == "gate" and state == "approved"):
            return ended(http, unit, answer)
        if http.clock() + EVERY > deadline:
            return out_of_time(http, unit, state, page)
        http.sleep(EVERY)
        try:
            later = retried(http, deadline, lambda: call(http, engine, "GET", f"/requests/{urllib.parse.quote(request)}"))
        except Refused as e:
            http.out(f"go-live: {e}")
            http.summary(f"### Go-live of {clean(unit)}\n\nThe engine stopped answering this run: {e}.\n")
            return 1
        if later is None:
            return out_of_time(http, unit, state, page)
        answer = later


def ended(http: Http, unit: str, answer: dict[str, Any]) -> int:
    state = answer["state"]
    error = clean((answer.get("result") or {}).get("error") or "")
    code = ENDED.get(state, 0)
    line = f"{SAID.get(state, clean(state))}{': ' + error if error and code else ''}"
    http.out(f"go-live: {clean(unit)} {line}")
    http.summary(f"### Go-live of {clean(unit)}\n\n{line[0].upper()}{line[1:]}.\n{needs_table(answer)}")
    return code


def out_of_time(http: Http, unit: str, state: str, page: str) -> int:
    said = ("still awaiting approval: the engine runs it when approved" if state in ("planning", "awaiting")
            else f"still {SAID.get(state, clean(state))}: the engine finishes it")
    http.out(f"go-live: the time is up, {said}")
    http.summary(f"### Go-live of {clean(unit)}\n\nThe time is up, {said}: [{page}]({page})\n")
    return 0


def send(method: str, url: str, headers: dict[str, str], data: bytes | None,
         timeout: float = 30.0) -> tuple[int, bytes]:  # pragma: no cover - the network
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return answer.status, answer.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def actions_token(env: dict[str, str], get: Callable[..., tuple[int, bytes]] = send,
                  out: Callable[[str], None] = print) -> Callable[[str], str]:
    """The run's OIDC token for an audience, asked of GitHub Actions each time, and
    masked in the log the first time it is seen."""
    url, bearer = env.get("ACTIONS_ID_TOKEN_REQUEST_URL"), env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not bearer:
        raise SystemExit("go-live: no OIDC token: the job needs `permissions: id-token: write`")

    def token(audience: str) -> str:
        joined = "&" if "?" in url else "?"
        try:
            status, raw = get("GET", f"{url}{joined}audience={urllib.parse.quote(audience, safe='')}",
                              {"Authorization": f"Bearer {bearer}", "Accept": "application/json"}, None)
            value = json.loads(raw).get("value") if status == 200 else None
        except (OSError, ValueError, AttributeError):
            value = None
        if not isinstance(value, str) or not value:
            raise Unanswered("GitHub gave no OIDC token")
        out(f"::add-mask::{value}")
        return value
    return token


def main(argv: list[str], env: dict[str, str] | None = None) -> int:  # pragma: no cover - the workflow's
    parser = argparse.ArgumentParser(prog="pubrules go-live", description=__doc__.split("\n")[0])
    parser.add_argument("--unit", required=True, help="the deploy unit (a fulfil), or what the gate is for")
    parser.add_argument("--kind", choices=KINDS, default="fulfil")
    parser.add_argument("--minutes", type=float, default=300.0, help="how long the run waits (300)")
    parser.add_argument("--engine", default=ENGINE, help=f"the convergence engine's address ({ENGINE})")
    parser.add_argument("--plan", default="", help="a gate's plan, which the approver reads (8 KB at most)")
    args = parser.parse_args(argv)
    env = dict(os.environ) if env is None else env
    summary_path = env.get("GITHUB_STEP_SUMMARY")

    def summary(text: str) -> None:
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write(text + "\n")
    http = Http(send=send, token=actions_token(env), summary=summary)
    return go_live(http, args.engine, args.unit, args.kind, args.minutes, args.plan)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
