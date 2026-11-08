"""The steps the pubkit deploy features share. pytest-bdd keeps a step in
the module that defines it, so each test_deploy_*.py takes them all with
`from deploy_steps import *`, the fixtures they are kept as included."""

from __future__ import annotations

import base64
import json
import sys
import threading
import time
import types as modules
from datetime import timedelta
from types import SimpleNamespace

import pytest
import yaml
from pytest_bdd import given, parsers, then, when

from deploy_world import CANARY, NOW, World, key_value, run, world  # noqa: F401 - the fixture
from pubkit.deploy import load
from pubkit.deploy.model import Result, Stop
from pubkit.deploy.registry import BUILTIN
from pubkit.deploy.schema import journal_schema, problems, validate


def value_of(text: str):
    return json.loads(text)


def entry(world: World, need_id: str) -> dict:
    """The need the block lists with that id."""
    from pubkit.deploy.registry import types  # noqa: PLC0415
    known = types()
    for e in world.block["needs"]:
        params = {k: v for k, v in e.items() if k not in ("type", "name", "after")}
        name = e.get("name") or known[e["type"]].default_name(params) or world.block["namespace"]
        if f"{e['type']}:{name}" == need_id:
            return e
    raise AssertionError(f"the block lists no {need_id}")


# --- the block ---------------------------------------------------------------


@given("the example service")
def example(world):
    pass


@given(parsers.parse('the deploy block\'s "{key}" is {value}'))
def block_key(world, key, value):
    if value == "absent":
        world.block.pop(key, None)
    else:
        world.block[key] = value_of(value)


@given(parsers.parse("the deploy block is {value}"))
def block_is(world, value):
    if value == "absent":
        del world.config["deploy"]
    else:
        world.config["deploy"] = value_of(value)


@given(parsers.parse("the deploy block also needs {need}"))
def also_needs(world, need):
    world.block["needs"].append(value_of(need))


@given(parsers.parse('the need "{need_id}" is dropped'))
def dropped(world, need_id):
    world.block["needs"].remove(entry(world, need_id))


@given(parsers.parse('the need "{need_id}" is after "{other}"'))
def is_after(world, need_id, other):
    entry(world, need_id).setdefault("after", []).append(other)


@given(parsers.parse('the file "{path}" is gone'))
def gone(world, path):
    (world.root / path).unlink()


@given("the deploy block lists its needs the other way round")
def reversed_needs(world):
    world.block["needs"].reverse()


@given(parsers.parse('pubkit.json\'s github_app block names the app "{app}"'))
def github_app(world, app):
    upper = app.upper().replace("-", "_")
    world.config["github_app"] = {slot: {"from": f"{app}-{slot.replace('_', '-')}", "to": f"{upper}_{slot.upper()}"}
                                  for slot in ("id", "installation_id", "key")}


@given(parsers.parse('the secrets block sends only "{to}"'))
def only_secret(world, to):
    world.config["secrets"] = [e for e in world.config["secrets"] if e["to"] == to]


@given("the example service's managed files are in step")
def in_step(world, capsys):
    """pubkit init's files, then sync's. sync is given no --zones, so the
    host's zone is in no hosting block it reads."""
    from pubkit.cli import main  # noqa: PLC0415
    (world.root / "pubkit.json").unlink()
    assert main(["init", "--kind=service", "--project=example"]) == 0
    world.save()
    assert main(["sync"]) == 0
    world.outputs += capsys.readouterr()
    world.zones = None


# --- packages offering need types ---------------------------------------------


class Thing:
    type, grade, budget, offline = "example.thing", "none", 1.0, True
    schema = {"type": "object", "additionalProperties": False, "properties": {"size": {"type": "integer"}}}

    def default_name(self, params):
        return None

    def depends_on(self, need, needs):
        return []

    def check(self, need, ctx):
        return Result("ready", "the thing is there")

    def fulfil(self, need, ctx):
        raise NotImplementedError("phase 2")


class Point:
    """An entry point of the sustenaut.needs group, from a distribution."""

    def __init__(self, name, value, dist):
        self.name, self.value, self.dist = name, value, SimpleNamespace(name=dist)

    def load(self):
        import importlib  # noqa: PLC0415
        module, _, attr = self.value.partition(":")
        return getattr(importlib.import_module(module), attr)


@pytest.fixture
def offered(monkeypatch):
    """The entry points the installed packages offer, and their module."""
    points: list[Point] = []
    module = modules.ModuleType("example_needs")
    monkeypatch.setitem(sys.modules, "example_needs", module)
    monkeypatch.setattr("pubkit.deploy.registry.entry_points", lambda group: list(points))
    return SimpleNamespace(points=points, module=module)


def offer(offered, dist, kind, attr):
    setattr(offered.module, attr, kind)
    offered.points.append(Point(kind.type if isinstance(getattr(kind, "type", None), str) else "thing",
                                f"example_needs:{attr}", dist))


@given(parsers.parse('the package "{dist}" offers the need type "{name}"'))
def offers(offered, dist, name):
    kind = Thing()
    kind.type = name
    offer(offered, dist, kind, name.upper().replace(".", "_").replace("-", "_").removeprefix("EXAMPLE_"))


@given(parsers.parse('the package "{dist}" offers the need type "{name}", whose schema takes any key'))
def offers_open(offered, dist, name):
    kind = Thing()
    kind.type = name
    kind.schema = {"type": "object"}
    setattr(offered.module, "BAD", kind)
    offered.points.append(Point("bad", "example_needs:BAD", dist))


@given(parsers.parse('the package "{dist}" offers a need type whose {part} is {value}'))
def offers_bad(offered, dist, part, value):
    kind = Thing()
    if part == "module":
        offered.points.append(Point("thing", f"{value_of(value)}:THING", dist))
        return
    setattr(kind, part, value_of(value))
    setattr(offered.module, "THING", kind)
    offered.points.append(Point("thing", "example_needs:THING", dist))


# --- running -------------------------------------------------------------------


@when(parsers.re(r'I run pubkit (?P<args>(?:"[^"]*" ?)+)'), target_fixture="result")
def run_pubkit(world, capsys, args):
    argv = args.strip().strip('"').split('" "')
    code = run(world, capsys, *argv)
    return SimpleNamespace(code=code, stdout=world.stdout, stderr=world.stderr)


@when("I check the deploy block")
def check_block(world):
    from pubkit.deploy.registry import types  # noqa: PLC0415
    world.problems = problems(world.config, world.root, types())


@when("I load the plan")
def load_plan(world):
    world.plan = load(world.root, world.config, world.context())
    assert not world.plan.problems, world.plan.problems


@when(parsers.parse('I load the plan for the namespace "{ns}"'))
def load_plan_in(world, ns):
    world.plan = load(world.root, world.config, world.context(namespace=ns), namespace=ns)


@then(parsers.parse('the only problem is "{text}"'))
def only_problem(world, text):
    assert world.problems == [text], world.problems


@then(parsers.parse('the only problem starts "{text}"'))
def only_problem_starts(world, text):
    assert len(world.problems) == 1 and world.problems[0].startswith(text), world.problems


@then("the problems are")
def the_problems(world, datatable):
    assert world.problems == [row[0] for row in datatable[1:]], world.problems


@then("there is no problem")
def no_problem(world):
    assert world.problems == [], world.problems


def plan(world):
    if world.plan is None:
        load_plan(world)
    return world.plan


@then(parsers.parse('the plan has {count:d} "{need_id}"'))
def plan_has(world, count, need_id):
    assert list(plan(world).needs).count(need_id) == count, list(plan(world).needs)


@then(parsers.parse('the plan has no "{need_id}"'))
def plan_lacks(world, need_id):
    assert need_id not in plan(world).needs, list(plan(world).needs)


@then("its needs are")
def needs_are(world, datatable):
    got = [(i, n.implied_by or "") for i, n in plan(world).needs.items()]
    assert got == [tuple(row) for row in datatable[1:]], got


@then("its needs include")
def needs_include(world, datatable):
    got = {(i, n.implied_by or "") for i, n in plan(world).needs.items()}
    for row in datatable[1:]:
        assert tuple(row) in got, (row, sorted(got))


@then("the walk order is")
def walk_order(world, datatable):
    assert plan(world).order == [row[0] for row in datatable[1:]], plan(world).order


@then("the walk order is the example's")
def walk_order_example(world, tmp_path):
    fresh = World(world.root)
    assert plan(world).order == load(fresh.root, fresh.config, fresh.context()).order


@then(parsers.parse('"{later}" comes after "{earlier}"'))
def comes_after(world, later, earlier):
    order = plan(world).order
    assert order.index(earlier) < order.index(later), order


@then(parsers.parse('"{need_id}" has the keys "{keys}" and the shape "{shape}" for "{key}"'))
def has_keys(world, need_id, keys, shape, key):
    params = plan(world).needs[need_id].params
    assert params["keys"] == keys.split(", ") and params["shapes"] == {key: shape}, params


@then(parsers.parse('the need "{need_id}" was implied by "{by}"'))
def implied_by(world, need_id, by):
    assert plan(world).needs[need_id].implied_by == by


@then(parsers.parse('the listing shows "{kind}" graded "{grade}", from "{dist}", taking "{keys}"'))
def listing(world, kind, grade, dist, keys):
    import re  # noqa: PLC0415
    rows = [re.split(r"\s{2,}", line.strip()) for line in world.stdout.splitlines()]
    row = next(r for r in rows if r[0] == kind)
    assert (row[1], row[4], row[6]) == (grade, dist, keys), row


# --- the walk's results ---------------------------------------------------------


def need_lines(world, kind: str) -> list[str]:
    """The walk's line of each need of a type."""
    return [line for line in world.stdout.splitlines()
            if not line.startswith(" ") and line.split()[1:2] and line.split()[1].startswith(kind + ":")]


def line_of(world, need_id: str) -> str:
    for line in world.stdout.splitlines():
        parts = line.split()
        if len(parts) > 1 and parts[1] == need_id:
            return line
    raise AssertionError(f"{need_id} is not in:\n{world.stdout}")


@then(parsers.parse('"{need_id}" is {state}, "{text}"'))
def state_with(world, need_id, state, text):
    line = line_of(world, need_id)
    assert line.split()[0] == state and text in line, line


@then(parsers.parse('"{need_id}" is {state}'))
def state_is(world, need_id, state):
    line = line_of(world, need_id)
    assert line.split()[0] == state, line


@then(parsers.parse('every "{kind}" need is {state}, "{text}"'))
def every_with(world, kind, state, text):
    lines = need_lines(world, kind)
    assert lines and all(line.split()[0] == state and text in line for line in lines), lines


@then(parsers.parse('every "{kind}" need is {state}'))
def every_is(world, kind, state):
    lines = need_lines(world, kind)
    assert lines and all(line.split()[0] == state for line in lines), lines


@then("nothing reached kubectl, GitHub or the network")
def nothing_reached(world):
    assert {k: world.calls[k] for k in ("kubectl", "gh", "http")} == {"kubectl": [], "gh": [], "http": []}, world.calls


@then("no output holds a value")
def no_value(world):
    assert not world.leaked()


# --- the journal ----------------------------------------------------------------


@then("the journal is a deploy-journal/2 document")
def journal_valid(world):
    assert validate(world.journal, journal_schema()) == [], validate(world.journal, journal_schema())


@then(parsers.parse('the journal\'s walk is offline, from "{origin}"'))
def journal_walk(world, origin):
    assert world.journal["walk"]["offline"] is True and world.journal["walk"]["from"] == origin


@then(parsers.parse("the journal lists the {count:d} needs in walk order, none with params"))
def journal_needs(world, count):
    fresh = World(world.root)
    order = load(fresh.root, fresh.config, fresh.context()).order
    assert [n["id"] for n in world.journal["needs"]] == order and len(order) == count
    assert all("params" not in n for n in world.journal["needs"])


@then(parsers.parse("the journal counts {ready:d} ready and {unknown:d} unknown"))
def journal_counts(world, ready, unknown):
    assert world.journal["summary"] == {"ready": ready, "missing": 0, "drift": 0, "blocked": 0, "unknown": unknown}


# --- the cluster ------------------------------------------------------------------


def namespace(world, ns="example"):
    return world.cluster["namespaces"][ns]


@given(parsers.parse('the namespace "{ns}" is not in the cluster'))
def no_namespace(world, ns):
    del world.cluster["namespaces"][ns]


@given(parsers.parse('the namespace "{ns}" has no "{kind}" "{name}"'))
def no_object(world, ns, kind, name):
    namespace(world, ns)[kind].discard(name)


@given(parsers.parse('the Secret "{secret}" holds "{key}" as 20 bytes'))
def holds_20(world, secret, key):
    namespace(world)["secrets"][secret][key] = (CANARY + "k" * 9).encode()


@given(parsers.parse('the Secret "{secret}" has no key "{key}"'))
def lacks_key(world, secret, key):
    data = namespace(world)["secrets"][secret]
    data.pop(key)
    data["other"] = f"{CANARY}-other".encode()


@given(parsers.parse('the Secret "{secret}" is not in the cluster'))
def no_secret(world, secret):
    del namespace(world)["secrets"][secret]


@given("kubectl may not read anything")
def kubectl_forbidden(world):
    world.cluster["forbidden"] = True


@given("kubectl is not installed")
def kubectl_missing(world):
    world.cluster["missing"] = True


@given(parsers.parse("kubectl takes {seconds:g} second to show a namespace"))
def kubectl_slow(world, seconds):
    world.slow["namespace"] = seconds


@given(parsers.parse('the Deployment "{name}" has {ready:d} of {want:d} replicas ready'))
def deployment_late(world, name, ready, want):
    d = next(d for d in namespace(world)["deployment"] if d["metadata"]["name"] == name)
    d["spec"]["replicas"], d["status"]["readyReplicas"] = want, ready


@given(parsers.parse('the CronJob "{name}" last succeeded {hours:d} hours ago'))
def cronjob_old(world, name, hours):
    namespace(world)["cronjob"][name]["status"]["lastSuccessfulTime"] = (NOW - timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


@given(parsers.parse('the CronJob "{name}" never succeeded'))
def cronjob_never(world, name):
    namespace(world)["cronjob"][name]["status"] = {}


# --- the manifests -----------------------------------------------------------------


@given(parsers.parse('every reference to "{secret}" in the manifests is optional'))
def optional(world, secret):
    docs = list(yaml.safe_load_all(world.rendered))

    def mark(node):
        if isinstance(node, dict):
            for key in ("secretRef", "secretKeyRef"):
                if isinstance(node.get(key), dict) and node[key].get("name") == secret:
                    node[key]["optional"] = True
            for v in node.values():
                mark(v)
        elif isinstance(node, list):
            for v in node:
                mark(v)
    mark(docs)
    world.rendered = yaml.safe_dump_all(docs)


@given(parsers.parse('the rendered manifests hold the Secret "{secret}"'))
def held(world, secret):
    world.rendered += (f"---\napiVersion: v1\nkind: Secret\nmetadata: {{name: {secret}, namespace: example}}\n"
                       f"stringData: {{LOG_LEVEL: {CANARY}-info}}\n")


@given(parsers.parse('kustomize fails with "{message}"'))
def kustomize_fails(world, message):
    world.render_error = Stop(f"kustomize build k8s failed: {message}")


@given("neither kustomize nor kubectl is installed")
def no_kustomize(world):
    world.render_error = Stop("neither kustomize nor kubectl is installed, to render k8s", missing_tool=True)


@given("the overlay does not render")
def no_render(world):
    world.render_error = Stop("kustomize build k8s failed: no")


@given(parsers.parse('the file "{path}" holds only a comment'))
def only_comment(world, path):
    (world.root / path).write_text("# nothing to apply\n")


@given(parsers.parse('the file "{path}" holds "{text}"'))
def file_holds(world, path, text):
    (world.root / path).write_text(text + "\n")


@given(parsers.parse('the need "{need_id}" fills the key "{key}"'))
def fills_key(world, need_id, key):
    entry(world, need_id)["secret"]["key"] = key


@given(parsers.parse('the need "{need_id}" fills no key'))
def fills_any(world, need_id):
    entry(world, need_id)["secret"].pop("key")


@given("no hosting block keeps the zone")
def no_zones(world):
    world.zones = None


@given("the host has no address")
def no_address(world):
    world.addresses = []


# --- GitHub ---------------------------------------------------------------------------


@given("GitHub refuses every request")
def github_forbidden(world):
    world.github["forbidden"] = True


@given("origin is not a GitHub repository")
def no_origin(world):
    world.slug = ""


@given(parsers.parse('the variable "{name}" is "{value}"'))
def variable_is(world, name, value):
    world.github["variables"][(None, name)] = value


@given(parsers.parse('the variable "{name}" is ""'))
def variable_empty(world, name):
    world.github["variables"][(None, name)] = ""


@given(parsers.parse('the variable "{name}" is not set'))
def variable_unset(world, name):
    del world.github["variables"][(None, name)]


@given(parsers.parse('GitHub holds no secret "{name}"'))
def no_github_secret(world, name):
    world.github["secrets"] = {s for s in world.github["secrets"] if s[1] != name}


@given(parsers.parse('GitHub holds no variable "{name}"'))
def no_github_variable(world, name):
    world.github["variables"] = {k: v for k, v in world.github["variables"].items() if k[1] != name}


@given(parsers.parse('main is at "{sha}"'))
def main_at(world, sha):
    world.github["head"] = sha + "0" * 33


@given(parsers.parse('the last deploy run ended "{conclusion}"'))
def run_ended(world, conclusion):
    world.github["runs"]["workflow_run"][0]["conclusion"] = conclusion


@given("the deploy workflow never ran")
def never_ran(world):
    world.github["runs"] = {"push": [], "workflow_run": []}


# --- HTTP ---------------------------------------------------------------------------------


@given(parsers.parse('"{url}" answers {status:d}'))
def answers(world, url, status):
    world.http[url] = status


@given(parsers.parse('"{url}" does not answer in time'))
def times_out(world, url):
    world.http[url] = "timeout"


@given(parsers.parse('"{url}" refuses the connection'))
def refuses(world, url):
    world.http.pop(url)


@given("nothing answers on the network")
def no_network(world):
    world.http.clear()


# --- Bitwarden, through ./with-secrets ----------------------------------------------------------


def env_name(name: str) -> str:
    return name.upper().replace("-", "_")


VALUES = {"20 bytes": (CANARY + "k" * 9),
          "another value": f"{CANARY}-other",
          "64 hex digits": "5e" * 32,
          "base64 of 32 bytes": base64.b64encode(b"\x5e" * 32).decode(),
          "base64 of 16 bytes": base64.b64encode(b"\x5e" * 16).decode()}


@given(parsers.parse('Bitwarden\'s "{name}" is {what}'))
def bitwarden_is(world, name, what):
    world.env[env_name(name)] = VALUES[what]
    if CANARY not in VALUES[what]:
        world.values.append(VALUES[what])


@given(parsers.parse('Bitwarden has no "{name}"'))
def bitwarden_lacks(world, name):
    del world.env[env_name(name)]


@given("the environment is not ./with-secrets'")
def outside(world):
    del world.env["PUBKIT_SECRETS_PROJECT"]


@given(parsers.parse('the need "{need_id}" has the shape "{shape}"'))
def has_shape(world, need_id, shape):
    entry(world, need_id)["shape"] = shape


# --- budgets ----------------------------------------------------------------------------------


def builtin(kind: str):
    return next(t for t in BUILTIN if t.type == kind)


@given(parsers.parse('the "{kind}" check\'s budget is {seconds:g} seconds'))
def budget(monkeypatch, kind, seconds):
    monkeypatch.setattr(builtin(kind), "budget", seconds)


@given(parsers.parse('the "{kind}" check takes {seconds:g} seconds, and the walk waits for it'))
def slow_check(monkeypatch, kind, seconds):
    t = builtin(kind)
    check = t.check

    def slow(need, ctx):
        time.sleep(seconds)
        return check(need, ctx)
    monkeypatch.setattr(t, "check", slow)
    join = threading.Thread.join
    monkeypatch.setattr(threading.Thread, "join", lambda self, timeout=None: join(self))


# --- fulfilling ---------------------------------------------------------------------------------


@given(parsers.parse('"{answer}" is typed at the prompt'))
def typed(world, answer):
    world.answers.append(answer)


@given("the walk has no terminal")
def no_terminal(world):
    world.terminal = False


@given("the walk runs in GitHub Actions")
def in_actions(world):
    world.env["GITHUB_ACTIONS"] = "true"


@given("kubectl may write nothing")
def no_writes(world):
    world.cluster["refuse_writes"] = True


@given("the walk reads the cluster over the deploy channel")
def deploy_channel(world):
    world.channel = "deploy"


@given("the server has no secret-keys verb")
def no_secret_keys(world):
    world.cluster["secret_keys_verb"] = False


@given("no command runs on the server")
def no_admin(world):
    world.admin = False


@given(parsers.parse('a deploy run by hand ends "{conclusion}"'))
def dispatch_ends(world, conclusion):
    world.github["dispatch_ends"] = conclusion


@given("Bitwarden refuses every write")
def bitwarden_refuses(world):
    world.bitwarden_refuses = True


WRITES = ("apply", "rollout", "create", "wait")


@then("kubectl was given nothing to write")
def nothing_written(world):
    assert not world.cluster.get("stdin"), "kubectl was given a manifest"
    assert not [c for c in world.calls["kubectl"] if any(w in c for w in WRITES)], world.calls["kubectl"]


@then(parsers.parse('the cluster\'s Secret "{secret}" holds Bitwarden\'s "{name}" as "{key}"'))
def holds_bitwarden(world, secret, name, key):
    assert namespace(world)["secrets"][secret] == {key: world.env[env_name(name)].encode()}
    value = base64.b64encode(world.env[env_name(name)].encode())
    assert any(value in given for given in world.cluster["stdin"]), "the value did not come on standard input"


@then(parsers.parse('the cluster\'s Secret "{secret}" holds what Bitwarden was given for "{name}" as "{key}"'))
def holds_written(world, secret, name, key):
    assert namespace(world)["secrets"][secret][key] == world.written[name].encode()


@then("the write token was asked for once")
def token_once(world):
    assert sum("Bitwarden access token" in o for o in world.outputs) == 1, world.outputs


@then("the write token was never asked for")
def token_never(world):
    assert not any("Bitwarden access token" in o for o in world.outputs)


@then(parsers.parse('Bitwarden was given "{name}", {size:d} bytes'))
def bitwarden_given(world, name, size):
    assert len(world.written[name]) == size, world.written.keys()


@then(parsers.parse('Bitwarden was given nothing'))
def bitwarden_nothing(world):
    assert world.written == {}


@then(parsers.parse('the server ran "{command}"'))
def server_ran(world, command):
    assert command.split() in [argv for argv, _ in world.calls.get("server", [])], world.calls.get("server")


@then(parsers.parse('deploy.yml was run on "{branch}" once'))
def dispatched_once(world, branch):
    assert world.github.get("dispatches") == [("deploy.yml", branch)], world.github.get("dispatches")


@then(parsers.parse('the variable "{name}" is still "{value}"'))
def variable_still(world, name, value):
    assert world.github["variables"][(None, name)] == value


@then(parsers.parse('gh set "{kind}" "{name}"'))
def gh_set(world, kind, name):
    assert any(s[0] == kind and s[1] == name for s in world.calls["sent"]), world.calls["sent"]


@then(parsers.parse('a Job was made from the CronJob "{name}"'))
def job_made(world, name):
    assert any(j.startswith(f"{name}-pubkit-") for j in world.cluster.get("jobs", [])), world.cluster.get("jobs")


@then(parsers.parse('stderr does not mention "{text}"'))
def stderr_lacks(world, text):
    assert text not in world.stderr, world.stderr


# --- the phase 2 types ------------------------------------------------------------------------------


@given(parsers.parse('Bitwarden holds "{name}" as the live key "{key_id}" of "{account}"'))
def live_key(world, name, key_id, account):
    world.env[env_name(name)] = key_value(key_id)
    world.values.append(world.env[env_name(name)])
    world.gcloud["keys"][account] = [key_id]


@given(parsers.parse('Bitwarden holds "{name}" as a key "{key_id}" the account does not have'))
def dead_key(world, name, key_id):
    world.env[env_name(name)] = key_value(key_id)
    world.values.append(world.env[env_name(name)])


@given("gcloud is not installed")
def no_gcloud(world):
    world.gcloud["missing"] = True


@then(parsers.parse('Bitwarden was given "{name}" as a key of "{account}"'))
def given_key(world, name, account):
    key = json.loads(base64.b64decode(world.written[name]))
    assert key["private_key_id"] in world.gcloud["keys"][account] and CANARY in key["private_key"]


@then(parsers.parse('"{account}" has no key'))
def no_key(world, account):
    assert world.gcloud["keys"].get(account) == [], world.gcloud["keys"]


@given("the service signs in with a client of example.eu")
def signs_in(world):
    (world.root.parent / "auth").mkdir(exist_ok=True)
    (world.root.parent / "auth" / "register-app.py").write_text("print('the registration')\n")
    (world.root.parent / "infra").mkdir(exist_ok=True)
    (world.root.parent / "infra" / "approvals.json").write_text("{}\n")
    world.block["needs"].append({"type": "auth.sign-in-client", "realm": "team", "role": "example",
                                 "auth": "https://auth.example.eu/realms/team", "domain": "example.eu",
                                 "state": "/root/.config/example-auth/team",
                                 "approvals": "../infra/approvals.json", "secret": {"name": "example-oidc"}})


@given(parsers.re(r'the client is "(?P<status>[a-z]+)"$'))
def client_is(world, status):
    world.auth = {"status": status}


@given(parsers.parse('the client is "waiting", with the record "{name}" "{value}"'))
def client_waits(world, name, value):
    world.auth = {"status": "waiting", "record": {"name": name, "value": value}}


@given(parsers.parse('the Secret "{secret}" holds the client'))
def holds_client(world, secret):
    namespace(world)["secrets"][secret] = {"client-id": f"{CANARY}-id".encode(),
                                           "client-secret": f"{CANARY}-secret".encode()}


@then(parsers.parse('register-app.py came on the server\'s standard input, with "{flag}"'))
def script_on_stdin(world, flag):
    runs = [(argv, given) for argv, given in world.calls["server"] if argv[:3] == ["python3", "-I", "-"]]
    assert runs and all(given == b"print('the registration')\n" and flag in argv for argv, given in runs), runs


@then(parsers.parse('the approvals file holds "{value}"'))
def approvals_hold(world, value):
    assert json.loads((world.root.parent / "infra" / "approvals.json").read_text()) == {"@": value}


TENANTS_PATH = "repos/example-org/storage/contents/tenants.json"


def tenants(world, given):
    world.github.setdefault("others", {})[TENANTS_PATH] = {
        "content": base64.b64encode(json.dumps(given).encode()).decode(), "encoding": "base64"}


@given("the service is a private tenant of the storage")
def tenant(world):
    world.block["needs"].append({"type": "storage.tenant", "name": "example", "storage": "example-org/storage",
                                 "url": "https://files.example.eu", "read": ["k8s:example/app"], "private": True})
    world.tenants = {"example": {"private": True, "read": ["k8s:example/app"], "write": ["k8s:example/app"]}}
    tenants(world, world.tenants)
    world.http["https://files.example.eu/example/list?prefix=f/"] = 401


@given(parsers.parse('the storage\'s tenants.json has no "{name}"'))
def no_tenant(world, name):
    del world.tenants[name]
    tenants(world, world.tenants)


@given(parsers.parse('the storage\'s tenant "{name}" lets nothing read'))
def no_readers(world, name):
    world.tenants[name]["read"] = []
    tenants(world, world.tenants)


ENTRY_PATH = "repos/example-org/releases/actions/variables/RELEASE_STATE"


def release_state(world, state):
    world.github.setdefault("others", {})[ENTRY_PATH] = {"name": "RELEASE_STATE", "value": json.dumps(state)}


@given("another repository's variable holds the entry")
def entry_held(world):
    world.block["needs"].append({"type": "github.variable-entry", "repository": "example-org/releases",
                                 "variable": "RELEASE_STATE", "path": "/evaluator", "value": {"version": "1.2.0"}})
    world.state = {"evaluator": {"version": "1.2.0"}, "other": {"token": CANARY}}
    release_state(world, world.state)


@given("the variable's entry is not there")
def entry_gone(world):
    del world.state["evaluator"]
    release_state(world, world.state)


@given("the variable's entry is another value")
def entry_other(world):
    world.state["evaluator"] = {"version": CANARY}
    release_state(world, world.state)


@given(parsers.parse('the CronJob "{name}" is suspended'))
def cronjob_suspended(world, name):
    namespace(world)["cronjob"][name]["spec"]["suspend"] = True


@given(parsers.parse('the CronJob "{name}" runs on "{schedule}"'))
def cronjob_schedule(world, name, schedule):
    namespace(world)["cronjob"][name]["spec"]["schedule"] = schedule


# --- units ---------------------------------------------------------------------------------------


@given(parsers.parse('the deploy block has the unit "{unit}", in "{ns}", from "{overlay}", gated by "{gate}"'))
def has_unit(world, unit, ns, overlay, gate):
    world.block.setdefault("units", {})[unit] = {"namespace": ns, "overlay": overlay, "gate": gate}
    (world.root / overlay).mkdir(exist_ok=True)
    (world.root / overlay / "kustomization.yaml").write_text("resources: []\n")


@when(parsers.parse('I load the plan for the unit "{unit}"'))
def load_unit(world, unit):
    world.plan = load(world.root, world.config, world.context(unit=unit), unit=unit)
    assert not world.plan.problems, world.plan.problems


@then(parsers.parse('the journals are of the units "{units}"'))
def journals_of(world, units):
    documents = world.journal if isinstance(world.journal, list) else [world.journal]
    assert [d["service"]["unit"] for d in documents] == units.split(", ")
    for d in documents:
        assert validate(d, journal_schema()) == []


@then(parsers.parse('the journal of "{unit}" says "{need_id}" is {state}'))
def journal_of_says(world, unit, need_id, state):
    document = next(d for d in world.journal if d["service"]["unit"] == unit)
    assert next(n for n in document["needs"] if n["id"] == need_id)["state"] == state


def caller(version: str, deploys: str, push: bool) -> str:
    lines = "".join(f"        {line.strip()}\n" for line in deploys.split(";"))
    on = "on:\n  push: { branches: [main] }\n" if push else (
        "on:\n  workflow_run: { workflows: [main], types: [completed], branches: [main] }\n")
    permissions = "permissions: { contents: read, packages: write }\n" if push else (
        "permissions: { actions: read, contents: read, packages: write }\n")
    return (f"name: deploy\n{on}{permissions}jobs:\n  deploy:\n"
            f"    uses: example-org/pubkit/.github/workflows/k8s-deploy.yml@{version}\n"
            f"    with:\n      image: ghcr.io/example-org/example\n      deploys: |\n{lines}    secrets: inherit\n")


@given(parsers.re(r'the deploy workflow calls k8s-deploy.yml "(?P<version>[^"]+)" deploying "(?P<deploys>[^"]+)"'
                  r'(?P<push>, on push)?$'))
def calls_k8s_deploy(world, version, deploys, push):
    (world.root / ".github" / "workflows" / "deploy.yml").write_text(caller(version, deploys, bool(push)))


@when("I check the workflow against the units")
def check_units(world):
    from pubkit.deploy.units import problems as unit_problems  # noqa: PLC0415
    world.problems = unit_problems(world.root, world.block)


@then(parsers.parse('the problem names "{first}" and "{second}"'))
def problem_names(world, first, second):
    assert first in world.problems[0] and second in world.problems[0], world.problems


# --- the journal in storage -------------------------------------------------------------------------


class FakeHeads:
    """Storage, as store.Heads speaks to it: files, and heads by path with ETags."""

    def __init__(self, world, base, tenant, token):
        self.world, self.base, self.tenant, self.token = world, base, tenant, token
        world.storage["signed_in"].append((base, tenant, token))

    def upload(self, data):
        from pubkit.board_publish import Refused  # noqa: PLC0415
        if self.world.storage.get("refuse"):
            raise Refused(403, b"no")
        self.world.storage["files"].append((self.tenant, json.loads(data)))
        return f"file-{len(self.world.storage['files'])}"

    def head(self, path):
        return self.world.storage["heads"].get(path)

    def point(self, path, hash_, current):
        self.world.storage["moves"].append((path, hash_, current))
        if self.world.storage.get("race"):
            self.world.storage["race"] = False
            self.world.storage["heads"][path] = "newer"
            return 412
        self.world.storage["heads"][path] = hash_
        return 200


@pytest.fixture(autouse=True)
def storage(world, monkeypatch):
    world.storage = {"signed_in": [], "files": [], "heads": {}, "moves": []}
    world.env["PUBKIT_STORAGE_TOKEN"] = f"{CANARY}-storage-token"
    world.values.append(f"{CANARY}-storage-token")
    monkeypatch.setattr("pubkit.deploy.store.Heads", lambda base, tenant, token: FakeHeads(world, base, tenant, token))


@given(parsers.parse('storage holds the head "{path}" at "{etag}"'))
def head_at(world, path, etag):
    world.storage["heads"][path] = etag


@given("another walk moves the head first, once")
def race(world):
    world.storage["race"] = True


@given("storage refuses every write")
def storage_refuses(world):
    world.storage["refuse"] = True


@given("there is no storage token")
def no_storage_token(world):
    del world.env["PUBKIT_STORAGE_TOKEN"]


@then(parsers.parse('storage was given a deploy-journal/2 document as a file of "{tenant}"'))
def storage_given(world, tenant):
    (given_tenant, document), = world.storage["files"]
    assert given_tenant == tenant and validate(document, journal_schema()) == []


@then(parsers.parse('the head "{path}" was moved If-Match "{etag}"'))
def moved(world, path, etag):
    assert world.storage["moves"] == [(path, "file-1", etag)], world.storage["moves"]


@then(parsers.parse('the head was moved twice, the second time If-Match "{etag}"'))
def moved_twice(world, etag):
    assert len(world.storage["moves"]) == 2 and world.storage["moves"][1][2] == etag, world.storage["moves"]


@then(parsers.parse('the journal\'s service is the unit "{unit}"'))
def journal_unit(world, unit):
    assert world.journal["service"]["unit"] == unit


@then(parsers.parse('the journal\'s walk is a "{mode}"'))
def journal_mode(world, mode):
    assert world.journal["walk"]["mode"] == mode


@then(parsers.parse('the journal grades "{first}" "{grade}" and "{second}" "{other}"'))
def journal_grades(world, first, grade, second, other):
    grades = {n["id"]: n["grade"] for n in world.journal["needs"]}
    assert (grades[first], grades[second]) == (grade, other), grades


@then("the journal fulfilled nothing")
def fulfilled_nothing(world):
    assert not any(n["fulfilled"] for n in world.journal["needs"])


@then(parsers.parse('the journal fulfilled "{need_id}" only'))
def fulfilled_only(world, need_id):
    assert [n["id"] for n in world.journal["needs"] if n["fulfilled"]] == [need_id]
