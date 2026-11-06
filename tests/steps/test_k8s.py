"""Steps for k8s-deploy.yml and the kustomize component k8s/components/app: the
sample service in tests/fixtures/k8s-app rendered with kustomize, and the
workflow parsed as GitHub reads it."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import yaml
from pytest_bdd import parsers, scenarios, then, when

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
    assert job["needs"] == "build"
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
