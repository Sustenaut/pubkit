"""Steps for `pubkit k8s preflight` and `pubkit k8s diagnose`, against a
fake kubectl on PATH that answers from a JSON file: the namespace's
Secrets, whether it may read them, its pods, their events and logs. It
records each call, and the steps check what no output may hold: a value."""

from __future__ import annotations

import base64
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("k8s-preflight.feature")

SOURCE = Path(__file__).resolve().parents[2] / "src" / "pubkit" / "k8s.py"

FAKE_KUBECTL = f"""#!{sys.executable}
import json, os, sys
state = json.load(open(os.environ["FAKE_K8S"]))
with open(os.environ["FAKE_K8S_CALLS"], "a") as calls:
    calls.write(json.dumps(sys.argv[1:]) + "\\n")
args = sys.argv[1:]
ns = args[args.index("-n") + 1]
if args[2:4] == ["get", "secret"]:
    if state.get("forbidden"):
        sys.exit('Error from server (Forbidden): secrets "' + args[4] + '" is forbidden: User cannot get resource')
    data = state["secrets"].get(ns + "/" + args[4])
    if data is None:
        sys.exit('Error from server (NotFound): secrets "' + args[4] + '" not found')
    print(json.dumps({{"kind": "Secret", "metadata": {{"name": args[4]}}, "data": data}}))
elif args[2:4] == ["get", "pods"]:
    print(json.dumps({{"items": state.get("pods", [])}}))
elif args[2:4] == ["get", "events"]:
    print(json.dumps({{"items": state.get("events", [])}}))
elif args[2] == "logs":
    key = args[3] + "/" + args[args.index("-c") + 1] + ("/previous" if "--previous" in args else "")
    print(state.get("logs", {{}}).get(key, ""), end="")
"""


def b64(value: bytes) -> str:
    return base64.b64encode(value).decode()


# The values a cookie secret is given as, by name. 0xfb bytes are + and /
# in standard base64.
COOKIE = {
    "standard base64 of 32 bytes": base64.b64encode(b"\xfb" * 32),
    "URL-safe base64 of 32 bytes": base64.urlsafe_b64encode(b"\xfb" * 32),
    "URL-safe base64 of 16 bytes, unpadded": base64.urlsafe_b64encode(b"\xfb" * 16).rstrip(b"="),
    "URL-safe base64 of 20 bytes": base64.urlsafe_b64encode(b"\xfb" * 20),
    "URL-safe base64 of 32 bytes, and a newline": base64.urlsafe_b64encode(b"\xfb" * 32) + b"\n",
    "32 bytes as they are": b"k" * 32,
    "24 bytes as they are": b"k" * 24,
    "20 bytes as they are": b"k" * 20,
}


def sample() -> list[dict]:
    """A service's rendered manifests: an app that reads a Secret whole, one
    key of another as a volume, and an optional one; oauth2-proxy with its
    cookie and client secrets; a CronJob whose init container's projected
    volume reads a key; and a Job reading a Secret the manifests hold."""
    def deployment(name, pod):
        return {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": name},
                "spec": {"template": {"spec": pod}}}

    def ref(name, key, **kw):
        return {"secretKeyRef": {"name": name, "key": key, **kw}}

    app = {"imagePullSecrets": [{"name": "ghcr-pull"}],
           "containers": [{"name": "app", "image": "ghcr.io/example/app:1",
                           "envFrom": [{"secretRef": {"name": "app-env"}}],
                           "env": [{"name": "EXTRA", "valueFrom": ref("extra", "x", optional=True)}]}],
           "volumes": [{"name": "files", "secret": {"secretName": "app-files",
                                                    "items": [{"key": "token", "path": "token"}]}}]}
    proxy = {"containers": [{"name": "oauth2-proxy", "image": "quay.io/oauth2-proxy/oauth2-proxy:v7.12.0",
                             "args": ["--provider=oidc"],
                             "env": [{"name": "OAUTH2_PROXY_COOKIE_SECRET",
                                      "valueFrom": ref("page-secrets", "cookie-secret")},
                                     {"name": "OAUTH2_PROXY_CLIENT_SECRET",
                                      "valueFrom": ref("page-secrets", "client-secret")}]}]}
    backup = {"initContainers": [{"name": "fetch", "image": "busybox"}],
              "containers": [{"name": "backup", "image": "busybox"}],
              "volumes": [{"name": "credentials", "projected": {"sources": [
                  {"secret": {"name": "backup-key", "items": [{"key": "key.json", "path": "key.json"}]}}]}}]}
    job = {"containers": [{"name": "migrate", "image": "busybox",
                           "env": [{"name": "A", "valueFrom": ref("held-config", "a")}]}]}
    return [
        deployment("app", app),
        deployment("proxy", proxy),
        {"apiVersion": "batch/v1", "kind": "CronJob", "metadata": {"name": "backup"},
         "spec": {"jobTemplate": {"spec": {"template": {"spec": backup}}}}},
        {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": "migrate"},
         "spec": {"template": {"spec": job}}},
        {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "held-config"},
         "stringData": {"a": "1"}},
        {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "app"}, "spec": {}},
    ]


@pytest.fixture
def cluster(tmp_path, monkeypatch):
    """The fake kubectl's state, written before each run, and its calls."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "fake-kubectl"
    fake.write_text(FAKE_KUBECTL)
    fake.chmod(0o755)
    state = {"secrets": {}, "values": []}
    monkeypatch.setenv("FAKE_K8S", str(tmp_path / "k8s.json"))
    monkeypatch.setenv("FAKE_K8S_CALLS", str(tmp_path / "calls"))
    (tmp_path / "calls").write_text("")
    state["kubectl"] = str(fake)
    return state


def put(cluster, secret, key, value: bytes):
    cluster["secrets"].setdefault(f"example/{secret}", {})[key] = b64(value)
    cluster["values"].append(value)


@given("the sample manifests", target_fixture="manifests")
def manifests():
    return sample()


def container(manifests, workload, name):
    doc = next(d for d in manifests if d["metadata"]["name"] == workload)
    return next(c for c in doc["spec"]["template"]["spec"]["containers"] if c["name"] == name)


@given(parsers.parse('the namespace "{ns}" holds every Secret the sample reads'))
def holds_all(cluster, ns):
    holds_but(cluster, ns, None)


@given(parsers.parse('the namespace "{ns}" holds every Secret the sample reads but "{missing}"'))
def holds_but(cluster, ns, missing):
    assert ns == "example"
    for secret, key, value in (("app-env", "LOG_LEVEL", b"info"), ("app-files", "token", b"t0ken-value"),
                               ("ghcr-pull", ".dockerconfigjson", b"{}"),
                               ("page-secrets", "cookie-secret", COOKIE["URL-safe base64 of 32 bytes"]),
                               ("page-secrets", "client-secret", b"client-secret-value"),
                               ("backup-key", "key.json", b'{"private_key": "pk"}')):
        if secret != missing:
            put(cluster, secret, key, value)


@given(parsers.parse('the Secret "{secret}" has no key "{key}"'))
def no_key(cluster, secret, key):
    del cluster["secrets"][f"example/{secret}"][key]


@given(parsers.parse('the cookie secret is "{value}"'))
def cookie_is(cluster, value):
    put(cluster, "page-secrets", "cookie-secret", COOKIE[value])


@given(parsers.parse('the Secret "{secret}" holds {key} as "{value}"'))
def holds_as(cluster, secret, key, value):
    put(cluster, secret, key.strip('"'), COOKIE[value])


@given(parsers.parse('the proxy is given --cookie-secret "{value}" as it is'))
def flag_literal(cluster, manifests, value):
    proxy = container(manifests, "proxy", "oauth2-proxy")
    proxy["args"] += ["--cookie-secret", COOKIE[value].decode()]
    cluster["values"].append(COOKIE[value])


@given(parsers.parse('the proxy is given --cookie-secret=$({var}) from the Secret "{secret}" key "{key}"'))
def flag_from_env(manifests, var, secret, key):
    proxy = container(manifests, "proxy", "oauth2-proxy")
    proxy["args"].append(f"--cookie-secret=$({var})")
    proxy["env"].append({"name": var, "valueFrom": {"secretKeyRef": {"name": secret, "key": key}}})


@given("the proxy's client secret names no key")
def no_key_named(manifests):
    proxy = container(manifests, "proxy", "oauth2-proxy")
    del proxy["env"][1]["valueFrom"]["secretKeyRef"]["key"]


@given("kubectl may not read Secrets")
def forbidden(cluster):
    cluster["forbidden"] = True


def write(tmp_path, cluster, manifests) -> Path:
    state = {k: v for k, v in cluster.items() if k not in ("values", "kubectl")}
    (tmp_path / "k8s.json").write_text(json.dumps(state))
    out = tmp_path / "manifests.yaml"
    out.write_text(yaml.safe_dump_all(manifests))
    return out


@when(parsers.parse('I preflight the sample in "{ns}"'), target_fixture="result")
def preflight(tmp_path, run, cluster, manifests, ns):
    path = write(tmp_path, cluster, manifests)
    from conftest import pubkit  # noqa: PLC0415
    return run(pubkit(), "k8s", "preflight", "--namespace", ns, "--kubectl", cluster["kubectl"],
               stdin=path.read_text())


@when("I preflight the sample offline", target_fixture="result")
def preflight_offline(tmp_path, run, cluster, manifests):
    path = write(tmp_path, cluster, manifests)
    from conftest import pubkit  # noqa: PLC0415
    return run(pubkit(), "k8s", "preflight", "--namespace", "example", "--offline",
               "--kubectl", cluster["kubectl"], stdin=path.read_text())


@when("I run the preflight's file alone, offline", target_fixture="result")
def file_alone(tmp_path, run, cluster, manifests):
    path = write(tmp_path, cluster, manifests)
    alone = tmp_path / "alone"
    alone.mkdir()
    shutil.copy(SOURCE, alone / "k8s.py")
    return run(sys.executable, "-I", str(alone / "k8s.py"), "preflight", "--namespace", "example",
               "--offline", stdin=path.read_text(), cwd=alone)


def calls(tmp_path) -> list[list[str]]:
    return [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]


@then("kubectl read each Secret once, and not held-config, which the manifests hold")
def read_once(tmp_path):
    read = [c[4] for c in calls(tmp_path) if c[2:4] == ["get", "secret"]]
    assert sorted(read) == ["app-env", "app-files", "backup-key", "extra", "ghcr-pull", "page-secrets"], read


@then("kubectl was not run")
def not_run(tmp_path):
    assert calls(tmp_path) == []


@then("no value of a Secret is printed")
def no_value(result, cluster):
    out = result.stdout + result.stderr
    out = out.decode() if isinstance(out, bytes) else out
    for value in cluster["values"]:
        for shown in {value.decode().strip(), b64(value)}:
            assert shown not in out, f"a value is printed: {out}"


@then(parsers.parse('it does not print "{text}"'))
def does_not_print(result, text):
    out = result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout
    assert text not in out, out


@given(parsers.parse('the pod "{pod}" crash-loops, its container "{name}" exiting 1 with "{line}"'))
def crash_loops(cluster, pod, name, line):
    cluster.setdefault("pods", []).append({
        "metadata": {"name": pod},
        "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "False"}],
                   "containerStatuses": [{
                       "name": name, "restartCount": 3,
                       "state": {"waiting": {"reason": "CrashLoopBackOff", "message": "back-off 40s"}},
                       "lastState": {"terminated": {"exitCode": 1, "reason": "Error"}}}]}})
    cluster.setdefault("events", []).append({
        "type": "Warning", "reason": "BackOff", "message": "Back-off restarting failed container",
        "involvedObject": {"name": pod}})
    cluster.setdefault("logs", {})[f"{pod}/{name}/previous"] = f"starting\n{line}\n"


@given(parsers.parse('the pod "{pod}" is ready'))
def ready(cluster, pod):
    cluster.setdefault("pods", []).append({
        "metadata": {"name": pod},
        "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}],
                   "containerStatuses": [{"name": "app", "restartCount": 0, "state": {"running": {}}}]}})


@when(parsers.parse('I diagnose "{ns}"'), target_fixture="result")
def diagnose(tmp_path, run, cluster, manifests, ns):
    write(tmp_path, cluster, manifests)
    from conftest import pubkit  # noqa: PLC0415
    return run(pubkit(), "k8s", "diagnose", "--namespace", ns, "--kubectl", cluster["kubectl"])
