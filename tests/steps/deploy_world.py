"""The world `pubkit deploy` scenarios run in: the example service of
tests/fixtures/deploy-app, copied, and fakes for everything a check
touches. kubectl answers from namespaces of Secrets, service accounts,
Deployments and CronJobs; the GitHub API from secrets, variables and
workflow runs; HTTP from a status per address; kustomize is the fixture's
rendered.yaml. Nothing runs kubectl, gh, kustomize or the network.

Every fake Secret value, every fake Bitwarden value and every variable's
value holds CANARY, and after each scenario no output, no journal and no
exception text may hold it (`world`'s teardown)."""

from __future__ import annotations

import copy
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from pubkit.deploy.model import Context, Ran, Stop, no_server
from pubkit.protect import ApiError

CANARY = "canary-7f3e"
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "deploy-app"
SLUG = "example-org/example"
HEAD = "abc1234" + "0" * 33
NOW = datetime(2026, 11, 7, 8, 0, tzinfo=UTC)
# A cookie secret oauth2-proxy takes as it is: 32 bytes.
COOKIE = (CANARY + "k" * 21).encode()


def good_cluster() -> dict[str, Any]:
    return {"forbidden": False, "namespaces": {"example": {
        "secrets": {"example-page-secrets": {"cookie-secret": COOKIE},
                    "example-files": {"token": f"{CANARY}-token".encode()},
                    "example-app-env": {"LOG_LEVEL": f"{CANARY}-info".encode()}},
        "serviceaccount": {"deploy"}, "role": {"deploy"}, "rolebinding": {"deploy"},
        "deployment": [{"metadata": {"name": n}, "spec": {"replicas": 1}, "status": {"readyReplicas": 1}}
                       for n in ("app", "proxy")],
        "cronjob": {"worker": {"spec": {"schedule": "0 * * * *"}, "status": {"lastSuccessfulTime": (NOW - timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")}}},
        "objects": {"networkpolicy/example-app"}}}}


def good_github() -> dict[str, Any]:
    return {"forbidden": False, "default_branch": "main", "head": HEAD,
            "secrets": {(None, "EXAMPLE_TOKEN"), (None, "TS_GH_OAUTH_CLIENT_ID"), (None, "TS_GH_OAUTH_SECRET"),
                        ("server", "SUSTENAUT_DEPLOY_SSH_KEY")},
            "variables": {(None, "EXAMPLE_USER"): f"{CANARY}-user", (None, "EXAMPLE_SETTING"): f"{CANARY}-setting",
                          (None, "DEPLOY_EXAMPLE"): "on", (None, "SUSTENAUT_SERVER"): f"{CANARY}.example.eu",
                          ("server", "SUSTENAUT_KNOWN_HOSTS"): f"{CANARY} ssh-ed25519 AAAA"},
            "runs": {"push": [], "workflow_run": [{"id": 7, "head_sha": HEAD, "conclusion": "success",
                                                   "status": "completed", "created_at": "2026-11-07T07:00:00Z"}]}}


def good_env() -> dict[str, str]:
    return {"PUBKIT_SECRETS_PROJECT": "example", "EXAMPLE_COOKIE_SECRET": COOKIE.decode(),
            "EXAMPLE_FILES_TOKEN": f"{CANARY}-token", "EXAMPLE_USER": f"{CANARY}-user",
            "EXAMPLE_TOKEN": f"{CANARY}-token", "SUSTENAUT_SERVER": "server.example.eu"}


class FakeKubectl:
    """kubectl's answers, from the cluster's state, the writes a fulfil
    makes into it, and the calls it got, with what came on standard input.
    With `channel` deploy it is the server's read-only verb: it refuses
    writes, and a Secret's data, and answers secret-keys unless the server
    has no such verb yet."""

    def __init__(self, state: dict[str, Any], calls: list, slow: dict[str, float], channel: str = "plain") -> None:
        self.state, self.calls, self.slow, self.channel = state, calls, slow, channel

    def __call__(self, args: list[str], input: bytes | None = None) -> Ran:  # noqa: A002
        import time  # noqa: PLC0415
        self.calls.append(args)
        if input is not None:
            self.state.setdefault("stdin", []).append(input)
        raw = list(args)
        args = [a for a in args if a not in ("-o", "json", "name")]
        if args[:2] == ["get", "namespace"] and self.slow.get("namespace"):
            time.sleep(self.slow["namespace"])
        if self.state.get("missing"):
            return Ran(127, "", "kubectl did not run (FileNotFoundError)")
        if self.state["forbidden"]:
            return Ran(1, "", 'Error from server (Forbidden): User "someone" cannot get resource')
        if args[:2] == ["get", "namespace"]:
            ns = args[2]
            if ns in self.state["namespaces"]:
                return Ran(0, json.dumps({"kind": "Namespace", "metadata": {"name": ns}}))
            return Ran(1, "", f'Error from server (NotFound): namespaces "{ns}" not found')
        ns, verb, rest = args[1], args[2], args[3:]
        space = self.state["namespaces"].get(ns)
        if self.channel == "deploy":
            return self.deploy(ns, verb, rest, raw, space)
        if verb != "get":
            return self.write(ns, verb, rest, input, space)
        kind = rest[0]
        name = rest[1] if len(rest) > 1 else None
        if kind in ("deployment", "job") and name is None:
            return Ran(0, json.dumps({"items": (space or {}).get(kind, [])}))
        if "/" in kind:
            found = space is not None and kind in space["objects"]
            return Ran(0, kind) if found else Ran(1, "", f'Error from server (NotFound): "{kind}" not found')
        held = (space or {}).get("secrets" if kind == "secret" else kind, {})
        if name not in held:
            return Ran(1, "", f'Error from server (NotFound): {kind}s "{name}" not found')
        if kind == "secret":
            import base64  # noqa: PLC0415
            data = {k: base64.b64encode(v).decode() for k, v in held[name].items()}
            return Ran(0, json.dumps({"kind": "Secret", "metadata": {"name": name}, "data": data}))
        if kind == "cronjob":
            return Ran(0, json.dumps(held[name]))
        return Ran(0, json.dumps({"metadata": {"name": name}}))

    def deploy(self, ns: str, verb: str, rest: list[str], raw: list[str], space: Any) -> Ran:
        """The read-only verb: get (a Secret only by name), and secret-keys."""
        held = (space or {}).get("secrets", {})
        if verb == "secret-keys":
            if not self.state.get("secret_keys_verb", True):
                return Ran(2, "", "kubectl-read: secret-keys is not a verb this server takes")
            if rest[0] not in held:
                return Ran(1, "", f'Error from server (NotFound): secrets "{rest[0]}" not found')
            return Ran(0, json.dumps(sorted(held[rest[0]])))
        if verb != "get":
            return Ran(2, "", f"kubectl-read: {verb} is not a verb this server takes")
        if rest[0] == "secret":
            if "-o" in raw and raw[raw.index("-o") + 1] != "name":
                return Ran(2, "", "kubectl-read: a Secret is shown by name only")
            if rest[1] not in held:
                return Ran(1, "", f'Error from server (NotFound): secrets "{rest[1]}" not found')
            # What a table of it would show, values and all, which nothing may keep.
            return Ran(0, f"secret/{rest[1]}" if "name" in raw else f"{rest[1]} {CANARY}")
        return self.__class__(self.state, [], self.slow)(["-n", ns, verb, *rest, "-o", "json"])

    def write(self, ns: str, verb: str, rest: list[str], input: bytes | None, space: Any) -> Ran:  # noqa: A002
        import base64  # noqa: PLC0415
        if self.state.get("refuse_writes"):
            return Ran(1, "", f'Error from server (Forbidden): cannot {verb} in "{ns}"')
        if space is None:
            return Ran(1, "", f'Error from server (NotFound): namespaces "{ns}" not found')
        if verb == "apply":
            assert rest == ["-f", "-"] and input is not None, rest
            out = []
            for doc in yaml.safe_load_all(input.decode()):
                if doc.get("kind") == "Secret":
                    space["secrets"][doc["metadata"]["name"]] = {k: base64.b64decode(v)
                                                                  for k, v in doc.get("data", {}).items()}
                else:
                    space["objects"].add(f"{doc['kind'].lower()}/{doc['metadata']['name']}")
                out.append(f"{doc['kind'].lower()}/{doc['metadata']['name']} configured")
            return Ran(0, "\n".join(out) + "\n")
        if verb == "rollout" and rest[0] == "restart":
            self.state.setdefault("restarted", []).append(rest[1])
            return Ran(0, f"{rest[1]} restarted")
        if verb == "create" and rest[0] == "job":
            cronjob = rest[1].removeprefix("--from=cronjob/")
            if cronjob not in space["cronjob"]:
                return Ran(1, "", f'Error from server (NotFound): cronjobs "{cronjob}" not found')
            # As kubectl makes it: not the CronJob's, so its success never moves the CronJob's status.
            self.state.setdefault("jobs", []).append(rest[2])
            space.setdefault("job", []).append({"metadata": {"name": rest[2]}, "status": {}})
            return Ran(0, f"job.batch/{rest[2]} created")
        if verb == "wait":
            if self.state.get("job_fails"):
                return Ran(1, "", "timed out waiting for the condition")
            job = next(j for j in space["job"] if j["metadata"]["name"] == rest[1].removeprefix("job/"))
            job["status"] = {"succeeded": 1, "completionTime": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")}
            return Ran(0, "condition met")
        raise AssertionError(f"kubectl {verb} {rest}")


class FakeServer:
    """Commands on the server, over the admin channel: sustenaut-app, and
    register-app.py's, each with its standard input kept, for the canary."""

    def __init__(self, world: Any) -> None:
        self.world = world

    def __call__(self, args: list[str], input: bytes | None = None) -> Ran:  # noqa: A002
        w = self.world
        w.calls.setdefault("server", []).append((list(args), input))
        if args[:1] == ["sustenaut-app"]:
            w.cluster["namespaces"].setdefault(args[1], {"secrets": {}, "serviceaccount": set(), "role": set(),
                                                         "rolebinding": set(), "deployment": [], "cronjob": {},
                                                         "objects": set()})
            for kind in ("serviceaccount", "role", "rolebinding"):
                w.cluster["namespaces"][args[1]][kind].add("deploy")
            return Ran(0, f"namespace {args[1]} ready")
        if args[:3] == ["python3", "-I", "-"] and "--status" in args:
            return Ran(0, json.dumps(w.auth))
        if args[:3] == ["python3", "-I", "-"] and args[3].endswith("/client.json"):
            assert input is not None and b"k3s" in input and len(args) == 6, args
            name, ns = args[4], args[5]
            w.cluster["namespaces"][ns]["secrets"][name] = {"client-id": f"{CANARY}-id".encode(),
                                                            "client-secret": f"{CANARY}-secret".encode()}
            return Ran(0, f"secret/{name} configured")
        raise AssertionError(f"the server was asked {args}")


class FakeWriter:
    """Bitwarden, with the write token: what it was given to write, by name."""

    def __init__(self, world: Any, token: str) -> None:
        self.world, self.token = world, token

    def write(self, name: str, value: str, note: str) -> None:
        assert CANARY in self.token
        if self.world.bitwarden_refuses:
            raise RuntimeError(f"Bitwarden said no to {name}")
        self.world.written[name] = value
        self.world.values.append(value)


class FakeApi:
    """The GitHub API, as protect.gh calls it: reads from the state, and
    the writes a fulfil makes (variables, workflow dispatches) into it."""

    def __init__(self, state: dict[str, Any], calls: list) -> None:
        self.state, self.calls = state, calls

    def __call__(self, method: str, path: str, body: Any = None) -> Any:
        self.calls.append((method, path))
        if self.state["forbidden"] or (method != "GET" and self.state.get("read_only")):
            raise ApiError(f"gh api -X {method} {path}: HTTP 403: Resource not accessible (HTTP 403)")
        if path.startswith("repos/") and not path.startswith(f"repos/{SLUG}"):
            return self.other(method, path)
        route = path.split("?")[0].removeprefix(f"repos/{SLUG}").strip("/").split("/")
        query = dict(p.split("=") for p in path.partition("?")[2].split("&") if p)
        environment = None
        if route[:1] == ["environments"]:
            environment, route = route[1], ["actions", *route[2:]]
        if method == "PATCH" and route[:2] == ["actions", "variables"]:
            if (environment, route[2]) not in self.state["variables"]:
                raise ApiError(f"gh api -X PATCH {path}: HTTP 404: Not Found (HTTP 404)")
            self.state["variables"][(environment, route[2])] = body["value"]
            return None
        if method == "POST" and route == ["actions", "variables"]:
            self.state["variables"][(environment, body["name"])] = body["value"]
            return None
        if method == "POST" and route[:2] == ["actions", "workflows"] and route[-1] == "dispatches":
            self.state.setdefault("dispatches", []).append((route[2], body["ref"]))
            self.state["runs"].setdefault("workflow_dispatch", []).insert(0, {
                "id": 8, "head_sha": self.state["head"], "conclusion": self.state.get("dispatch_ends", "success"),
                "status": "completed", "created_at": "2026-11-07T08:00:01Z"})
            return None
        assert method == "GET", path
        if route == [""]:
            return {"default_branch": self.state["default_branch"]}
        if route[:1] == ["branches"]:
            return {"commit": {"sha": self.state["head"]}}
        if route[:2] == ["actions", "workflows"]:
            return {"workflow_runs": self.state["runs"].get(query.get("event"), [])}
        if route == ["actions", "secrets"] or route == ["actions", "variables"]:
            kind = route[1]
            held = self.state[kind]
            return {kind: [{"name": n} for e, n in held if e == environment]}
        if route[:2] == ["actions", "secrets"] and (environment, route[2]) in self.state["secrets"]:
            return {"name": route[2]}
        if route[:2] == ["actions", "variables"] and (environment, route[2]) in self.state["variables"]:
            return {"name": route[2], "value": self.state["variables"][(environment, route[2])]}
        raise ApiError(f"gh api -X GET {path}: HTTP 404: Not Found (HTTP 404)")

    def other(self, method: str, path: str) -> Any:
        """Another repository's contents and variables, from state["others"]."""
        found = self.state.get("others", {}).get(path.split("?")[0])
        if method != "GET" or found is None:
            raise ApiError(f"gh api -X {method} {path}: HTTP 404: Not Found (HTTP 404)")
        return found


class FakeSend:
    """app_secrets.send: what it set, by name, the value checked for the canary and dropped."""

    def __init__(self, github: dict[str, Any], sent: list) -> None:
        self.github, self.sent = github, sent

    def __call__(self, kind: str, name: str, repo: str, value: str, environment: str | None = None) -> str | None:
        assert repo == SLUG and value
        self.sent.append((kind, name, environment, len(value)))
        if self.github.get("refuse_send"):
            return f"HTTP 403: may not set {name} ({value})"
        if kind == "secret":
            self.github["secrets"].add((environment, name))
        else:
            self.github["variables"][(environment, name)] = value
        return None


class FakeHttp:
    """A status per address, or "timeout" or "refused"."""

    def __init__(self, answers: dict[str, Any], calls: list) -> None:
        self.answers, self.calls = answers, calls

    def __call__(self, url: str, timeout: float) -> tuple[int, bytes]:
        self.calls.append(url)
        answer = self.answers.get(url, "refused")
        if answer == "timeout":
            raise TimeoutError
        if answer == "refused":
            raise ConnectionRefusedError(f"{url} refused, {CANARY}")
        return answer, f"body {CANARY}".encode()


class World:
    """One scenario's service, fakes and what came out of it."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.config = json.loads((root / "pubkit.json").read_text())
        self.rendered = (root / "rendered.yaml").read_text()
        self.render_error: Stop | None = None
        self.cluster = good_cluster()
        self.cluster["namespaces"]["example"]["deployment"] = deployments(self.rendered)
        self.github = good_github()
        self.http: dict[str, Any] = {"https://storage.example.eu/healthz": 200,
                                     "https://example.example.eu/healthz": 200}
        self.env = good_env()
        self.zones = json.loads((root / "zones.json").read_text())["hosting"]["zones"]
        self.slow: dict[str, float] = {}
        self.slug = SLUG
        self.addresses = ["192.0.2.1"]
        self.calls: dict[str, list] = {"kubectl": [], "gh": [], "http": []}
        self.channel = "plain"
        self.admin = True  # a command runs on the server
        self.auth: dict[str, Any] = {"status": "registered"}
        self.written: dict[str, str] = {}  # what the Bitwarden writer was given
        self.bitwarden_refuses = False
        self.token = f"{CANARY}-write-token"
        self.writer: Any = None  # the context's writer; the command's comes from the token
        self.answers: list[str] = []  # what is typed at the walk's prompts
        self.terminal = True
        self.gcloud: dict[str, Any] = {"accounts": {"example-files@example-project.iam.gserviceaccount.com"},
                                       "keys": {}, "missing": False}
        # Values that cannot hold the canary (hex, base64), which no output may hold either.
        self.values: list[str] = []
        self.plan: Any = None
        self.problems: list[str] | None = None
        self.outputs: list[str] = []
        self.code: int | None = None
        self.journal: dict[str, Any] | None = None

    @property
    def block(self) -> dict[str, Any]:
        return self.config["deploy"]

    def need(self, kind: str) -> dict[str, Any]:
        return next(n for n in self.block["needs"] if n["type"] == kind)

    def save(self) -> None:
        (self.root / "pubkit.json").write_text(json.dumps(self.config, indent=2))

    def render(self) -> str:
        if self.render_error:
            raise self.render_error
        return self.rendered

    def context(self, config: dict[str, Any] | None = None, offline: bool = False,
                namespace: str | None = None, unit: str | None = None) -> Context:
        from pubkit.deploy.units import unit_block  # noqa: PLC0415
        config = config or self.config
        unit = unit or config["deploy"]["namespace"]
        block = unit_block(config["deploy"], unit)
        return Context(root=self.root, config=config, block=block, unit=unit,
                       namespace=namespace or block["namespace"],
                       kubectl=FakeKubectl(self.cluster, self.calls["kubectl"], self.slow, self.channel),
                       channel=self.channel, bitwarden_writer=self.writer,
                       server=FakeServer(self) if self.admin and self.channel != "deploy" else no_server,
                       gh=FakeApi(self.github, self.calls["gh"]), http_get=FakeHttp(self.http, self.calls["http"]),
                       send=FakeSend(self.github, self.calls.setdefault("sent", [])), sleep=lambda _s: None,
                       env=dict(self.env), now=lambda: NOW, rendered=self.render, offline=offline,
                       slug=self.slug, zones=copy.deepcopy(self.zones),
                       resolve=lambda host, rtype: list(self.addresses) if rtype == "A" else [])

    def leaked(self) -> list[str]:
        """Every output, journal and exception text that holds a value, and
        every argv a command was given: a value goes on standard input."""
        seen = [*self.outputs, json.dumps(self.journal or {}),
                *(json.dumps(c) for c in self.calls["kubectl"]),
                *(json.dumps(argv) for argv, _ in self.calls.get("server", [])),
                *(json.dumps(c) for c in self.calls.get("gcloud", [])),
                *(json.dumps(c) for c in self.calls.get("sent", []))]
        return [text for text in seen for value in [CANARY, *self.values, self.token] if value in text]


def deployments(rendered: str) -> list[dict[str, Any]]:
    """The cluster's Deployments, as the rendered manifests make them, ready:
    pubsec's k8s.secret restarts those whose pods read the Secret it made."""
    return [{"metadata": {"name": d["metadata"]["name"]}, "spec": {"replicas": 1, "template": d["spec"]["template"]},
             "status": {"readyReplicas": 1}}
            for d in yaml.safe_load_all(rendered) if isinstance(d, dict) and d.get("kind") == "Deployment"]


@pytest.fixture
def world(tmp_path, monkeypatch, capsys):
    """The world, and after the scenario the canary: no value reached an
    output, the journal or an exception's text."""
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root)
    w = World(root)
    monkeypatch.chdir(root)
    monkeypatch.setenv("PUBKIT_ANY_VERSION", "1")

    def context(root, config, offline, kubectl_command=None, zones_file=None, namespace=None, server=None,
                unit=None):
        return w.context(config, offline, namespace, unit)
    monkeypatch.setattr("pubkit.deploy.context", context)
    monkeypatch.setattr("pubkit.deploy.ask", lambda prompt: w.outputs.append(prompt) or (w.answers.pop(0)
                                                                                         if w.answers else ""))
    # The types that hold a secret's value, and the hook that asks for the
    # write token, are pubsec's, a test dependency: the walk loads them as in use.
    import pubsec.walk  # noqa: PLC0415
    monkeypatch.setattr(pubsec.walk, "_writers", {})
    monkeypatch.setattr(pubsec.walk, "ask_hidden", lambda prompt: w.outputs.append(prompt) or w.token)
    monkeypatch.setattr(pubsec.walk, "BitwardenWriter", lambda token, project: FakeWriter(w, token))
    monkeypatch.setattr("sys.stdin.isatty", lambda: w.terminal, raising=False)
    import pubsec.needs.bitwarden as bitwarden  # noqa: PLC0415
    made = bitwarden.generate

    def generate(shape):
        value = made(shape)
        w.values.append(value)
        return value
    monkeypatch.setattr(bitwarden, "generate", generate)
    monkeypatch.setattr("pubsec.needs.gcp.gcloud", lambda args: fake_gcloud(w, args))
    yield w
    w.outputs += capsys.readouterr()
    assert not w.leaked(), f"a value was printed: {w.leaked()[0][:2000]}"


def fake_gcloud(w: World, args: list[str]) -> Ran:
    """gcloud's answers about service accounts and their keys; a key it
    makes holds the canary in its private key."""
    import base64  # noqa: PLC0415
    w.calls.setdefault("gcloud", []).append(args)
    if w.gcloud["missing"]:
        return Ran(127, "", "gcloud is not installed")
    flags = dict(a.removeprefix("--").split("=", 1) for a in args if a.startswith("--") and "=" in a)
    if args[:3] == ["iam", "service-accounts", "describe"]:
        there = args[3] in w.gcloud["accounts"]
        return Ran(0, args[3]) if there else Ran(1, "", f"NOT_FOUND: Unknown service account {args[3]}")
    account = flags.get("iam-account", "")
    keys = w.gcloud["keys"].setdefault(account, [])
    if args[3] == "list":
        return Ran(0, "\n".join(keys) + "\n")
    if args[3] == "create":
        key_id = f"{len(keys) + 1:x}" * 40
        keys.append(key_id[:40])
        return Ran(0, json.dumps({"type": "service_account", "private_key_id": key_id[:40],
                                  "private_key": f"-----BEGIN PRIVATE KEY-----\n{CANARY}\n-----END PRIVATE KEY-----"}))
    if args[3] == "delete":
        keys.remove(args[4])
        return Ran(0, "")
    raise AssertionError(args)


def key_value(key_id: str) -> str:
    """A key as Bitwarden keeps it: base64 of its JSON."""
    import base64  # noqa: PLC0415
    return base64.b64encode(json.dumps({"private_key_id": key_id, "private_key": CANARY}).encode()).decode()


def run(world: World, capsys, *argv: str) -> int:
    """`pubkit ARGV`, in-process, in the world's repository: its exit code,
    with what it printed kept for the canary and the steps."""
    from pubkit.cli import main  # noqa: PLC0415
    world.save()
    try:
        code = main(list(argv))
    except SystemExit as e:
        world.outputs.append(str(e.code))
        code = e.code if isinstance(e.code, int) else 1
    out, err = capsys.readouterr()
    world.outputs += [out, err]
    world.stdout, world.stderr, world.code = out, err, code
    if "--json" in argv and out.strip():
        world.journal = json.loads(out)
    return code
