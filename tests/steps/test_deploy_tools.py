"""The tools `pubkit deploy check` reaches outside a test's fakes: kustomize,
kubectl, HTTP, dig and git, each given a stand-in, never the real one, and
the command run whole, as a person runs it."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

from deploy_world import CANARY, FIXTURE
from pubkit import deploy
from pubkit.deploy.model import Context, Result, Stop
from pubkit.deploy.walk import timed


def program(bin_dir: Path, name: str, script: str) -> None:
    """A stand-in for a tool on PATH: a Python script."""
    bin_dir.mkdir(exist_ok=True)
    path = bin_dir / name
    path.write_text(f"#!{sys.executable}\nimport sys, json\n{script}\n")
    path.chmod(0o755)


@pytest.fixture
def tools(tmp_path, monkeypatch):
    """A PATH of only the stand-ins, and Python and git."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("git",):
        os.symlink(shutil.which(tool), bin_dir / tool)
    monkeypatch.setenv("PATH", str(bin_dir))
    return bin_dir


def test_render_runs_kustomize_build(tools, tmp_path):
    program(tools, "kustomize", "print('kind: ConfigMap') if sys.argv[1:2] == ['build'] else sys.exit(3)")
    assert deploy.render(tmp_path, "k8s") == "kind: ConfigMap\n"


def test_render_falls_back_to_kubectl_kustomize(tools, tmp_path):
    program(tools, "kubectl", "print('kind: Secret') if sys.argv[1:2] == ['kustomize'] else sys.exit(3)")
    assert deploy.render(tmp_path, "k8s") == "kind: Secret\n"


def test_render_says_what_kustomize_said(tools, tmp_path):
    program(tools, "kustomize", "print('warning', file=sys.stderr); print('Error: no such file', file=sys.stderr); "
                                "sys.exit(1)")
    with pytest.raises(Stop) as stop:
        deploy.render(tmp_path, "k8s")
    assert str(stop.value) == "kustomize build k8s failed: Error: no such file" and not stop.value.missing_tool


def test_render_without_either_tool(tools, tmp_path):
    with pytest.raises(Stop) as stop:
        deploy.render(tmp_path, "k8s")
    assert stop.value.missing_tool and "neither kustomize nor kubectl is installed" in str(stop.value)


def test_render_is_done_once(tools, tmp_path):
    program(tools, "kustomize", "import pathlib; p = pathlib.Path(sys.argv[2]).parent / 'count'; "
                                "p.write_text(p.read_text() + 'x' if p.exists() else 'x'); print('kind: List')")
    rendered = deploy.renderer(tmp_path, "k8s")
    assert rendered() == rendered() == "kind: List\n"
    assert (tmp_path / "count").read_text() == "x"


def test_kubectl_runs_the_command_given(tools):
    program(tools, "k3s", "print(json.dumps(sys.argv[1:]))")
    ran = deploy.kubectl("k3s kubectl --kubeconfig 'a file'")(["get", "namespace", "example"])
    assert ran.returncode == 0 and json.loads(ran.stdout) == ["kubectl", "--kubeconfig", "a file", "get",
                                                             "namespace", "example"]


def test_kubectl_that_is_not_there(tools):
    ran = deploy.kubectl("kubectl")(["get", "namespace", "example"])
    assert ran.returncode == 127 and ran.stderr == "kubectl did not run (FileNotFoundError)"


def test_resolve_asks_dig_for_addresses(tools):
    program(tools, "dig", "print('example.example.eu.') if 'CNAME' in sys.argv else print('192.0.2.1\\nhost.example.eu.')")
    assert deploy.resolve("example.example.eu", "A") == ["192.0.2.1"]


def test_resolve_without_dig(tools):
    assert deploy.resolve("example.example.eu", "A") is None


class Answer(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_http_get(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: Answer(b"ok"))
    assert deploy.http_get("https://example.example.eu/healthz", 1.0) == (200, b"ok")


def test_http_get_of_an_error_status(monkeypatch):
    def refuse(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 503, "Unavailable", {}, io.BytesIO(CANARY.encode()))
    monkeypatch.setattr("urllib.request.urlopen", refuse)
    assert deploy.http_get("https://example.example.eu/healthz", 1.0) == (503, b"")


@pytest.mark.parametrize(("reason", "raised"), [(TimeoutError(), TimeoutError),
                                                (ConnectionRefusedError(), ConnectionError)])
def test_http_get_that_is_not_answered(monkeypatch, reason, raised):
    def fail(request, timeout):
        raise urllib.error.URLError(reason)
    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(raised):
        deploy.http_get("https://example.example.eu/healthz", 1.0)


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   check=True, capture_output=True)


@pytest.mark.parametrize(("url", "slug"), [("git@github.com:example-org/example.git", "example-org/example"),
                                           ("https://github.com/example-org/example", "example-org/example"),
                                           ("https://example.eu/example.git", "")])
def test_slug_of_origin(tmp_path, url, slug):
    git(tmp_path, "init", "-q")
    git(tmp_path, "remote", "add", "origin", url)
    assert deploy.slug(tmp_path) == slug


def test_slug_outside_a_repository(tmp_path):
    assert deploy.slug(tmp_path) == ""


def test_zones_come_from_the_config_and_the_zones_file(tmp_path):
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"hosting": {"zones": {"example.org": {"sites": {"@": {}}}}}}))
    config = {"hosting": {"zones": {"example.eu": {"sites": {}}}}}
    assert set(deploy.zones_of(config, other)) == {"example.eu", "example.org"}
    assert deploy.zones_of({}, None) is None


@pytest.mark.parametrize(("env", "origin"), [({}, "laptop"), ({"GITHUB_ACTIONS": "true", "GITHUB_ACTOR": "someone"},
                                                               "actions"), ({"PUBKIT_PLANE": "1"}, "plane")])
def test_the_journal_says_where_the_walk_ran(tmp_path, env, origin):
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root)
    config = json.loads((root / "pubkit.json").read_text())
    ctx = Context(root=root, config=config, block=config["deploy"], namespace="example", env=env, offline=True,
                  rendered=lambda: (root / "rendered.yaml").read_text())
    journal = deploy.check(deploy.load(root, config, ctx), ctx)
    assert journal.walk["from"] == origin
    assert journal.walk["by"] == env.get("GITHUB_ACTOR", journal.walk["by"])


def test_a_check_that_returns_no_result_is_unknown():
    class Odd:
        type, budget = "example.odd", 1.0

        def check(self, need, ctx):
            return "ready"

    from pubkit.deploy.model import Need  # noqa: PLC0415
    result = timed(Odd(), Need("example.odd", "one", {}), Context.for_tests())
    assert (result.state, result.summary) == ("unknown", "the check returned no result (str)")


def test_every_builtin_fulfil_waits_for_phase_2():
    from pubkit.deploy.model import Need  # noqa: PLC0415
    from pubkit.deploy.registry import BUILTIN  # noqa: PLC0415
    for kind in BUILTIN:
        with pytest.raises(NotImplementedError, match="phase 2"):
            kind.fulfil(Need(kind.type, "one", {}), Context.for_tests())


def test_a_summary_is_one_line_of_at_most_120_characters():
    result = Result("ready", "a\nb " + "c" * 200)
    assert "\n" not in result.summary and len(result.summary) == 120


def test_the_context_for_tests_reaches_nothing():
    from pubkit.protect import ApiError  # noqa: PLC0415
    ctx = Context.for_tests()
    assert ctx.kubectl(["get", "namespace", "example"]).returncode == 127
    assert ctx.resolve("example.example.eu", "A") is None
    with pytest.raises(ApiError):
        ctx.gh("GET", "repos/example-org/example", None)
    with pytest.raises(ConnectionRefusedError):
        ctx.http_get("https://example.example.eu", 1.0)
    with pytest.raises(Stop):
        ctx.rendered()


def test_deploy_check_runs_whole(tmp_path, run):
    """The command as a person runs it: its own kustomize, kubectl and git
    stand-ins on PATH, offline, with --zones outside the repository."""
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root)
    bin_dir = tmp_path / "bin"
    program(bin_dir, "kustomize", f"print(open({str(root / 'rendered.yaml')!r}).read())")
    zones = tmp_path / "infra" / "pubkit.json"
    zones.parent.mkdir()
    shutil.move(root / "zones.json", zones)
    from conftest import pubkit  # noqa: PLC0415
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "PUBKIT_ANY_VERSION": "1"}
    done = run(pubkit(), "deploy", "check", "--offline", "--zones", str(zones), cwd=root, env=env)
    assert done.code == 0, done.describe()
    assert "ready   k8s.secrets-declared:example" in done.stdout
    done = run(pubkit(), "deploy", "check", "--offline", "--zones", str(tmp_path / "gone" / "pubkit.json"),
               cwd=root, env=env)
    assert done.code == 2 and "--zones" in done.stderr


@pytest.mark.parametrize(("path", "why"), [("../infra/zones.json", "must be a repository's pubkit.json"),
                                           ("/pubkit.json", "must be in the working directory")])
def test_deploy_check_reads_only_a_pubkit_json_beside_the_repository(tmp_path, run, path, why):
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root)
    from conftest import pubkit  # noqa: PLC0415
    done = run(pubkit(), "deploy", "check", "--offline", "--zones", path, cwd=root, env={"PUBKIT_ANY_VERSION": "1"})
    assert done.code != 0 and why in done.stderr, done.describe()
