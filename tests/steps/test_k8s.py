"""Steps for k8s-deploy.yml and the kustomize component k8s/components/app: the
sample service in tests/fixtures/k8s-app rendered with kustomize, and the
workflow parsed as GitHub reads it."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("k8s.feature")

SOURCE = Path(__file__).resolve().parents[2]
SAMPLE = SOURCE / "tests" / "fixtures" / "k8s-app"
WORKFLOW = SOURCE / ".github" / "workflows"


@when(parsers.parse('I parse the workflow "{name}"'), target_fixture="workflow")
def parse(name):
    return {"data": yaml.safe_load((WORKFLOW / name).read_text())}


def kustomize() -> list[str]:
    """kustomize, or kubectl's own where it is not installed."""
    if shutil.which("kustomize"):
        return ["kustomize", "build"]
    return ["kubectl", "kustomize"]


@when("I render the sample service with kustomize", target_fixture="rendered")
def render():
    out = subprocess.run([*kustomize(), str(SAMPLE)], check=True, capture_output=True, text=True).stdout
    return {doc["metadata"]["name"]: doc for doc in yaml.safe_load_all(out) if doc["kind"] == "Deployment"}


def pod(rendered, name):
    return rendered[name]["spec"]["template"]["spec"]


def container(rendered, name):
    return next(c for c in pod(rendered, "app")["containers"] if c["name"] == name)


@then(parsers.parse('the Deployment "{name}" runs as user 1000, not root, under the runtime\'s seccomp profile'))
def pod_hardened(rendered, name):
    context = pod(rendered, name)["securityContext"]
    assert context["runAsNonRoot"] is True and context["runAsUser"] == 1000
    assert context["seccompProfile"] == {"type": "RuntimeDefault"}


@then(parsers.parse('its container "{name}" can\'t gain privileges, has no capabilities and can\'t write its root filesystem'))
def container_hardened(rendered, name):
    assert container(rendered, name)["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }


@then(parsers.parse('its container "{name}" starts on /healthz, lives on /livez and is ready on /healthz, on the port "{port}"'))
def probes(rendered, name, port):
    app = container(rendered, name)
    for probe, path in (("startupProbe", "/healthz"), ("livenessProbe", "/livez"), ("readinessProbe", "/healthz")):
        assert app[probe]["httpGet"] == {"path": path, "port": port}, probe
    # A minute to start, then a minute without /livez before a restart.
    assert app["startupProbe"]["periodSeconds"] * app["startupProbe"]["failureThreshold"] == 60
    assert app["livenessProbe"]["periodSeconds"] * app["livenessProbe"]["failureThreshold"] == 60


@then(parsers.parse('the Deployment "{name}" keeps its own fsGroup'))
def keeps_own(rendered, name):
    assert pod(rendered, name)["securityContext"]["fsGroup"] == 1000


@then(parsers.parse('the Deployment "{name}" has no securityContext and no probes'))
def untouched(rendered, name):
    spec = pod(rendered, name)
    assert "securityContext" not in spec and "automountServiceAccountToken" not in spec
    for c in spec["containers"]:
        assert not {"securityContext", "startupProbe", "livenessProbe", "readinessProbe"} & set(c), c


@then(parsers.parse('the Deployment "{name}" mounts its service account token'))
def token(rendered, name):
    assert pod(rendered, name)["automountServiceAccountToken"] is True


def steps(workflow, job):
    return workflow["data"]["jobs"][job]["steps"]


def step(workflow, job, uses):
    return next(s for s in steps(workflow, job) if s.get("uses", "").startswith(uses))


@then("its build job builds with docker-build, runs the check, then pushes to ghcr.io")
def build_job(workflow):
    names = [s.get("uses", s.get("name", "")) for s in steps(workflow, "build")]
    build = next(i for i, n in enumerate(names) if n.startswith("Sustenaut/pubkit/actions/docker-build@"))
    check, push = names.index("Check the image"), names.index("Push the image")
    assert build < check < push
    check_step = steps(workflow, "build")[check]
    assert check_step["if"] == "inputs.check != ''" and check_step["run"] == "${{ inputs.check }}"
    assert "docker login ghcr.io" in steps(workflow, "build")[push]["run"]
    assert workflow["data"]["jobs"]["build"]["permissions"] == {"contents": "read", "packages": "write"}


@then("its deploy job runs once per unit, in the environment the caller names, one run at a time per unit")
def deploy_job(workflow):
    job = workflow["data"]["jobs"]["deploy"]
    assert job["needs"] == ["ready", "build"]
    assert job["environment"] == "${{ inputs.environment }}"
    assert job["strategy"]["matrix"]["include"] == "${{ fromJSON(needs.build.outputs.deploys) }}"
    assert job["concurrency"] == {"group": "deploy-${{ matrix.unit }}", "cancel-in-progress": False}
    assert job["permissions"] == {"contents": "read"}


@then("it deploys only when SUSTENAUT_SERVER is set and the unit's variable, if any, is on")
def gated(workflow):
    on = next(s for s in steps(workflow, "deploy") if s.get("id") == "gate")
    assert on["env"]["SERVER"] == "${{ vars.SUSTENAUT_SERVER }}"
    assert on["env"]["GATE_VALUE"] == "${{ matrix.gate != '' && vars[matrix.gate] || '' }}"
    assert 'if [[ -z "$SERVER" ]]' in on["run"] and '"$GATE_VALUE" != on' in on["run"]
    deploy = step(workflow, "deploy", "Sustenaut/pubkit/actions/deploy-ssh@")
    assert deploy["if"] == "steps.gate.outputs.deploy == 'true'"


@then("it deploys with deploy-ssh, the unit as the command and the rendered manifests as input, over the tailnet")
def deploys(workflow):
    with_ = step(workflow, "deploy", "Sustenaut/pubkit/actions/deploy-ssh@")["with"]
    assert with_["command"] == "${{ matrix.unit }}"
    assert with_["input"] == "${{ runner.temp }}/${{ matrix.unit }}.yaml"
    assert with_["target"] == "deploy@${{ vars.SUSTENAUT_SERVER }}"
    assert with_["deploy-key"] == "${{ secrets.SUSTENAUT_DEPLOY_SSH_KEY }}"
    assert with_["known-hosts"] == "${{ vars.SUSTENAUT_KNOWN_HOSTS }}"
    assert with_["tailscale-oauth-client-id"] == "${{ secrets.TS_GH_OAUTH_CLIENT_ID }}"
    assert with_["tailscale-oauth-secret"] == "${{ secrets.TS_GH_OAUTH_SECRET }}"
    prepare = next(s for s in steps(workflow, "deploy") if s.get("name") == "Prepare the overlay")
    assert prepare["env"] == {"VARS": "${{ toJSON(vars) }}"} and prepare["run"] == "${{ inputs.render }}"
    render = next(s for s in steps(workflow, "deploy") if s.get("name") == "Render the manifests")
    assert render["env"]["OUT"] == with_["input"]


READY = "${{ needs.ready.outputs.sha }}"


@then("its ready job runs for a workflow_run only when that run passed on a push to the default branch")
def ready_if(workflow):
    condition = " ".join(workflow["data"]["jobs"]["ready"]["if"].split())
    assert condition == ("github.event_name != 'workflow_run' || (github.event.workflow_run.conclusion == 'success'"
                         " && github.event.workflow_run.event == 'push'"
                         " && github.event.workflow_run.head_branch == github.event.repository.default_branch)")


@then("its build and deploy jobs need ready, and check out, tag and deploy the commit ready gives")
def ready_sha(workflow):
    jobs = workflow["data"]["jobs"]
    assert jobs["build"]["needs"] == "ready" and "ready" in jobs["deploy"]["needs"]
    for job in ("build", "deploy"):
        checkout = step(workflow, job, "actions/checkout@")
        assert checkout["with"]["ref"] == READY, job
    build = step(workflow, "build", "Sustenaut/pubkit/actions/docker-build@")
    assert build["with"]["tags"] == "${{ inputs.image }}:" + READY
    for name in ("Check the image", "Push the image"):
        s = next(s for s in steps(workflow, "build") if s.get("name") == name)
        assert s["env"]["IMAGE"] == "${{ inputs.image }}:" + READY, name
    for name in ("Set the image", "Render the manifests", "Is it deployed?"):
        s = next(s for s in steps(workflow, "deploy") if s.get("name") == name)
        assert s["env"]["SHA"] == READY, name
    text = (WORKFLOW / "k8s-deploy.yml").read_text()
    assert "GITHUB_SHA" not in text and text.count("github.sha") == 1  # ready's, outside a workflow_run


@then("its ready job may read the repository's runs, and waits for main and, where there is one, integrations")
def ready_reads(workflow):
    data = workflow["data"]
    assert data["jobs"]["ready"]["permissions"] == {"actions": "read", "contents": "read"}
    inputs = data[True]["workflow_call"]["inputs"]  # YAML 1.1 reads "on" as true
    assert inputs["require"]["default"] == "main ?integrations"
    assert inputs["wait"]["default"] == 30


# The ready job's and the deploy gate's scripts run as GitHub runs them,
# against a fake gh answering from a JSON file: the repository's
# workflows, the runs on the commit at each successive call, and the head
# of its default branch.
SHA = "a" * 40
HEAD = "b" * 40
FAKE_GH = f"""#!{sys.executable}
import json, os, subprocess, sys
state = json.load(open(os.environ["FAKE_GH"]))
args = [a for a in sys.argv[2:] if a != "--paginate"]
endpoint, jq = args[0], args[args.index("--jq") + 1]
if endpoint.endswith("/actions/workflows"):
    answer = {{"workflows": [{{"name": n}} for n in state["workflows"]]}}
elif "/actions/runs?" in endpoint:
    count = os.environ["FAKE_GH"] + ".count"
    n = int(open(count).read()) if os.path.exists(count) else 0
    open(count, "w").write(str(n + 1))
    answer = {{"workflow_runs": state["runs"][min(n, len(state["runs"]) - 1)]}}
elif "/branches/" in endpoint:
    answer = {{"commit": {{"sha": state["head"]}}}}
else:
    sys.exit("fake gh: " + endpoint)
sys.stdout.write(subprocess.run(["jq", "-r", jq], input=json.dumps(answer), capture_output=True,
                                text=True, check=True).stdout)
"""


@pytest.fixture
def gh(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    return {"workflows": [], "runs": [], "head": SHA, "bin": bin_dir}


def runs(text):
    """'"main" passed and "integrations" passed' as the runs on the commit."""
    out = []
    for part in text.split(" and "):
        name, said = part.split('" ')
        status, conclusion = {"passed": ("completed", "success"), "failed": ("completed", "failure"),
                              "is running": ("in_progress", None)}[said]
        out.append({"name": name.strip('"'), "status": status, "conclusion": conclusion,
                    "created_at": "2026-11-07T00:00:00Z", "html_url": f"https://example.invalid/{name}"})
    return out


@given(parsers.parse('the repository\'s workflows are "{names}"'))
def workflows(gh, names):
    gh["workflows"] = names.split(", ")


@given(parsers.parse("on the commit {said}"))
def on_commit(gh, said):
    gh["runs"].append(runs(said.removeprefix("only ")))


@given(parsers.parse("then {said}"))
def then_runs(gh, said):
    gh["runs"].append(runs(said))


@given("the default branch has moved on from the commit")
def moved_on(gh):
    gh["head"] = HEAD


def script(job, name):
    data = yaml.safe_load((WORKFLOW / "k8s-deploy.yml").read_text())
    return next(s for s in data["jobs"][job]["steps"] if s.get("name") == name)["run"]


def run_script(tmp_path, gh, text, env):
    state = tmp_path / "gh.json"
    state.write_text(json.dumps({k: v for k, v in gh.items() if k != "bin"}))
    out = tmp_path / "output"
    out.write_text("")
    path = tmp_path / "step.sh"
    path.write_text(text)
    done = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", str(path)],
                          capture_output=True, text=True, check=False, timeout=60,
                          env={"PATH": f"{gh['bin']}:/usr/bin:/bin", "FAKE_GH": str(state),
                               "GITHUB_OUTPUT": str(out), "GH_TOKEN": "t", "REPO": "example/service",
                               **env})
    return {"code": done.returncode, "stdout": done.stdout + done.stderr, "stderr": done.stderr,
            "output": out.read_text()}


class Result:
    def __init__(self, done):
        self.code, self.stdout, self.stderr, self.output = (
            done["code"], done["stdout"], done["stderr"], done["output"])


@when("ready runs for the commit", target_fixture="result")
def ready_runs(tmp_path, gh):
    return ready_requiring(tmp_path, gh, "main ?integrations")


@when(parsers.parse('ready runs for the commit, requiring "{require}"'), target_fixture="result")
def ready_requiring(tmp_path, gh, require):
    (tmp_path / "gh.json.count").unlink(missing_ok=True)
    return Result(run_script(tmp_path, gh, script("ready", "Wait for the commit's checks"),
                             {"SHA": SHA, "REQUIRE": require, "WAIT": "1", "GRACE": "0", "INTERVAL": "0"}))


@then("ready gives the commit")
def gives(result):
    assert result.output == f"sha={SHA}\n", result.output


@then("ready gives no commit")
def gives_none(result):
    assert result.output == "", result.output


@when(parsers.parse('the deploy gate runs for a "{event}"'), target_fixture="result")
def gate_runs(tmp_path, gh, event):
    return Result(run_script(tmp_path, gh, script("deploy", "Is it deployed?"),
                             {"SERVER": "server.example", "UNIT": "example", "GATE": "", "GATE_VALUE": "",
                              "EVENT": event, "BRANCH": "main", "SHA": SHA}))


@then("the gate deploys")
def deploys_now(result):
    assert result.code == 0 and result.output == "deploy=true\n", result.stdout


@then("the gate does not deploy")
def does_not_deploy(result):
    assert result.output == "", result.output
