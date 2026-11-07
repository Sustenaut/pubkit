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

from pubkit.deploy.model import Context, Ran, Stop
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
        "cronjob": {"worker": {"status": {"lastSuccessfulTime": (NOW - timedelta(hours=1)).strftime(
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
    """kubectl's answers, from the cluster's state, and the calls it got."""

    def __init__(self, state: dict[str, Any], calls: list, slow: dict[str, float]) -> None:
        self.state, self.calls, self.slow = state, calls, slow

    def __call__(self, args: list[str]) -> Ran:
        import time  # noqa: PLC0415
        self.calls.append(args)
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
        ns, verb, kind, *rest = args[1], args[2], *args[3:]
        assert verb == "get", args
        space = self.state["namespaces"].get(ns)
        name = rest[0] if rest else None
        if kind == "deployment" and name is None:
            return Ran(0, json.dumps({"items": (space or {}).get("deployment", [])}))
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


class FakeApi:
    """The GitHub API, as protect.gh calls it."""

    def __init__(self, state: dict[str, Any], calls: list) -> None:
        self.state, self.calls = state, calls

    def __call__(self, method: str, path: str, body: Any = None) -> Any:
        self.calls.append((method, path))
        assert method == "GET", path
        if self.state["forbidden"]:
            raise ApiError(f"gh api -X GET {path}: HTTP 403: Resource not accessible (HTTP 403)")
        route = path.split("?")[0].removeprefix(f"repos/{SLUG}").strip("/").split("/")
        query = dict(p.split("=") for p in path.partition("?")[2].split("&") if p)
        environment = None
        if route[:1] == ["environments"]:
            environment, route = route[1], ["actions", *route[2:]]
        if route == [""]:
            return {"default_branch": self.state["default_branch"]}
        if route[:1] == ["branches"]:
            return {"commit": {"sha": self.state["head"]}}
        if route[:2] == ["actions", "workflows"]:
            return {"workflow_runs": self.state["runs"].get(query.get("event"), [])}
        if route[:2] == ["actions", "secrets"] and (environment, route[2]) in self.state["secrets"]:
            return {"name": route[2]}
        if route[:2] == ["actions", "variables"] and (environment, route[2]) in self.state["variables"]:
            return {"name": route[2], "value": self.state["variables"][(environment, route[2])]}
        raise ApiError(f"gh api -X GET {path}: HTTP 404: Not Found (HTTP 404)")


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
        self.github = good_github()
        self.http: dict[str, Any] = {"https://storage.example.eu/healthz": 200,
                                     "https://example.example.eu/healthz": 200}
        self.env = good_env()
        self.zones = json.loads((root / "zones.json").read_text())["hosting"]["zones"]
        self.slow: dict[str, float] = {}
        self.slug = SLUG
        self.addresses = ["192.0.2.1"]
        self.calls: dict[str, list] = {"kubectl": [], "gh": [], "http": []}
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
                namespace: str | None = None) -> Context:
        config = config or self.config
        return Context(root=self.root, config=config, block=config["deploy"],
                       namespace=namespace or config["deploy"]["namespace"],
                       kubectl=FakeKubectl(self.cluster, self.calls["kubectl"], self.slow),
                       gh=FakeApi(self.github, self.calls["gh"]), http_get=FakeHttp(self.http, self.calls["http"]),
                       env=dict(self.env), now=lambda: NOW, rendered=self.render, offline=offline,
                       slug=self.slug, zones=copy.deepcopy(self.zones),
                       resolve=lambda host, rtype: list(self.addresses) if rtype == "A" else [])

    def leaked(self) -> list[str]:
        """Every output, journal and exception text that holds a value."""
        seen = [*self.outputs, json.dumps(self.journal or {})]
        return [text for text in seen for value in [CANARY, *self.values] if value in text]


@pytest.fixture
def world(tmp_path, monkeypatch, capsys):
    """The world, and after the scenario the canary: no value reached an
    output, the journal or an exception's text."""
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root)
    w = World(root)
    monkeypatch.chdir(root)
    monkeypatch.setenv("PUBKIT_ANY_VERSION", "1")

    def context(root, config, offline, kubectl_command="kubectl", zones_file=None, namespace=None):
        return w.context(config, offline, namespace)
    monkeypatch.setattr("pubkit.deploy.context", context)
    yield w
    w.outputs += capsys.readouterr()
    assert not w.leaked(), f"a value was printed: {w.leaked()[0][:2000]}"


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
