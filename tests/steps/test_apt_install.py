"""Steps for the apt-install action: its plan and install steps run as
GitHub runs a composite step, with the inputs (or their defaults) in the
environment each step's `env` gives, and stand-ins on PATH for sudo (runs
the command), dpkg (amd64), lsb_release, sleep (records its seconds) and
apt-get, which records each run and fills the cache directory as an
update or a download would."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

import yaml
from pytest_bdd import parsers, scenarios, then, when

scenarios("apt-install.feature")

ACTIONS = Path(__file__).resolve().parents[2] / "actions"
ACTION = ACTIONS / "apt-install"
INPUT = re.compile(r"^\$\{\{ inputs\.([\w-]+) \}\}$")
STEP = re.compile(r"^\$\{\{ steps\.(\w+)\.outputs\.([\w-]+) \}\}$")

# Records its arguments but the -c configuration, which it copies beside
# them; an update writes lists, a download writes packages, each with the
# lock and partial download apt leaves. $HOW says which runs fail.
APT_GET = r"""#!/bin/bash
conf="" args=()
while [ $# -gt 0 ]; do
  case "$1" in -c) conf="$2"; shift 2 ;; *) args+=("$1"); shift ;; esac
done
echo "${args[*]}" >> "$APT_LOG"
n=$(wc -l < "$APT_LOG")
[ -z "$conf" ] || cp "$conf" "$APT_LOG.$n.conf"
offline=false
[[ " ${args[*]} " == *" --no-download "* ]] && offline=true
case "$HOW" in
  lacking) [ "$offline" = false ] || exit 100 ;;
  flaky) [ "${args[0]}" = update ] || [ -e "$APT_LOG.failed" ] || { touch "$APT_LOG.failed"; exit 100; } ;;
  down) [ "${args[0]}" = update ] || exit 100 ;;
esac
if [ "${args[0]}" = update ]; then
  mkdir -p "$DIR/lists/partial" && touch "$DIR/lists/mirror_Packages" "$DIR/lists/lock" "$DIR/lists/partial/next"
elif [ "$offline" = false ]; then
  mkdir -p "$DIR/archives/partial" && touch "$DIR/archives/qemu-user.deb" "$DIR/archives/lock" "$DIR/archives/partial/next.deb"
fi
"""


def _action():
    return yaml.safe_load((ACTION / "action.yml").read_text())


def _stubs(work, codename):
    bin_dir = work / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    stubs = {"sudo": 'exec "$@"\n', "dpkg": "echo amd64\n", "sleep": 'echo "$1" >> "$SLEPT"\n',
             "apt-get": APT_GET.split("\n", 1)[1]}
    if codename:
        stubs["lsb_release"] = f"echo {codename}\n"
    for name, body in stubs.items():
        (bin_dir / name).write_text("#!/bin/bash\n" + body)
        (bin_dir / name).chmod(0o755)
    return bin_dir


def run_step(work, step_id, given, outputs, extra_env=None, codename="noble"):
    """Run the action's step `step_id` in `work`, as `bash -e`, with `given`
    inputs (the rest at their defaults) and `outputs` as the earlier steps'
    outputs; return its exit code, output and the outputs it wrote."""
    action = _action()
    inputs = {key: str(spec.get("default", "")) for key, spec in action["inputs"].items()}
    unknown = set(given) - set(inputs)
    assert not unknown, f"apt-install has no input {unknown}"
    # A "\n" in a scenario's input is a newline, as a YAML block gives.
    inputs.update({key: value.replace("\\n", "\n") for key, value in given.items()})
    (step,) = [s for s in action["runs"]["steps"] if s.get("id") == step_id]
    env = {}
    for key, value in step["env"].items():
        if m := INPUT.match(value):
            env[key] = inputs[m[1]]
        else:
            m = STEP.match(value)
            env[key] = outputs.get(m[1], {}).get(m[2], "")
    bin_dir = _stubs(work, codename)
    out = work / f"{step_id}.out"
    out.write_text("")
    base = {k: v for k, v in os.environ.items() if k not in ("ImageOS", "ImageVersion")}
    done = subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
        capture_output=True, text=True, check=False, cwd=work,
        env={**base, **env, **(extra_env or {}),
             "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "GITHUB_ACTION_PATH": str(ACTION),
             "RUNNER_TEMP": str(work / "temp"), "GITHUB_OUTPUT": str(out),
             "GITHUB_RUN_ID": "7", "GITHUB_RUN_ATTEMPT": "2",
             "SLEPT": str(work / "slept"), "APT_LOG": str(work / "apt.log")})
    written = dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line)
    return {"code": done.returncode, "stdout": done.stdout, "stderr": done.stderr, "outputs": written}


def _image(os_name, version):
    return {"ImageOS": os_name, "ImageVersion": version}


@when(parsers.re(r'apt-install plans "(?P<packages>[^"]*)" on the runner image "(?P<os>[^"]+)" "(?P<version>[^"]+)"$'),
      target_fixture="plans")
def plans_one(tmp_path, packages, os, version, request):
    earlier = request.getfixturevalue("plans") if "plans" in request.fixturenames else []
    work = tmp_path / f"plan{len(earlier)}"
    work.mkdir()
    return [*earlier, run_step(work, "plan", {"packages": packages}, {}, _image(os, version))]


@when(parsers.re(r'apt-install plans "(?P<first>[^"]*)" and "(?P<second>[^"]*)" on the runner image '
                 r'"(?P<os>[^"]+)" "(?P<version>[^"]+)"(?:, the second with no-install-recommends "(?P<flag>[^"]+)")?$'),
      target_fixture="plans")
def plans_two(tmp_path, first, second, os, version, flag):
    ran = []
    for n, (packages, given_flag) in enumerate(((first, "true"), (second, flag or "true"))):
        work = tmp_path / f"plan{n}"
        work.mkdir()
        ran.append(run_step(work, "plan", {"packages": packages, "no-install-recommends": given_flag}, {},
                            _image(os, version)))
    return ran


@when(parsers.parse('apt-install plans "{packages}" on a runner with no image, of the release "{codename}"'),
      target_fixture="plans")
def plans_self_hosted(tmp_path, packages, codename):
    return [run_step(tmp_path, "plan", {"packages": packages}, {}, codename=codename)]


@when(parsers.re(r'apt-install plans "(?P<packages>[^"]*)" with no-install-recommends "(?P<flag>[^"]*)"$'),
      target_fixture="plans")
def plans_flag(tmp_path, packages, flag):
    return [run_step(tmp_path, "plan", {"packages": packages, "no-install-recommends": flag}, {},
                     _image("ubuntu24", "20261110.1"))]


@then("the plan passes")
def plan_passes(plans):
    assert plans[-1]["code"] == 0, plans[-1]


@then(parsers.parse("the plan failed with {code:d}"))
def plan_failed(plans, code):
    assert plans[-1]["code"] == code, plans[-1]


@then(parsers.parse('the restore prefix is "{start}", the sha256 of "{text}" and "{end}"'))
def prefix_is(plans, start, text, end):
    digest = hashlib.sha256(text.encode().decode("unicode_escape").encode()).hexdigest()
    assert plans[-1]["outputs"]["prefix"] == start + digest + end, plans[-1]


@then(parsers.parse('the key is the restore prefix and "{run}", the run and its attempt'))
def key_is(plans, run):
    assert plans[-1]["outputs"]["key"] == plans[-1]["outputs"]["prefix"] + run, plans[-1]


@then(parsers.parse('the restore prefix starts "{start}"'))
def prefix_starts(plans, start):
    assert re.fullmatch(re.escape(start) + "[0-9a-f]{64}-", plans[-1]["outputs"]["prefix"]), plans[-1]


@then(parsers.parse('the cache directory is "{name}" in the runner\'s temporary directory'))
def cache_dir(plans, tmp_path, name):
    assert plans[-1]["outputs"]["dir"] == str(tmp_path / "plan0" / "temp" / name), plans[-1]


@then(parsers.re(r"the two keys (?P<how>are the same|differ)"))
def two_keys(plans, how):
    assert all(ran["code"] == 0 for ran in plans[-2:]), plans
    first, second = (ran["outputs"]["prefix"] for ran in plans[-2:])
    assert (first == second) == (how == "are the same"), (first, second)


@then(parsers.parse('it reported the error "{error}"'))
def reported(plans, error):
    assert any(line.startswith("::error::apt-install: ") and error in line
               for line in plans[-1]["stdout"].splitlines()), plans[-1]


# The key actions/cache restored: an earlier run's entry, or none.
HITS = {"on a hit": "5-1", "on a hit whose packages the cache lacks": "5-1",
        "on a miss": None, "after a restore that failed": None}
MIRRORS = {None: "works", "fails the first time": "flaky", "always fails": "down"}


@when(parsers.re(r'apt-install installs "(?P<packages>[^"]+)" (?P<when>on a hit whose packages the cache lacks|'
                 r"on a hit|on a miss|after a restore that failed)"
                 r'(?:, with a mirror that (?P<mirror>fails the first time|always fails))?'
                 r'(?:, with no-install-recommends "(?P<flag>[^"]+)")?$'), target_fixture="ran")
def installs(tmp_path, packages, when, mirror, flag):
    given = {"packages": packages, **({"no-install-recommends": flag} if flag else {})}
    plan = run_step(tmp_path, "plan", given, {}, _image("ubuntu24", "20261110.1"))
    assert plan["code"] == 0, plan
    how = "lacking" if "lacks" in when else MIRRORS[mirror]
    matched = "" if HITS[when] is None else plan["outputs"]["prefix"] + HITS[when]
    outputs = {"plan": plan["outputs"], "restore": {"cache-matched-key": matched}}
    ran = run_step(tmp_path, "install", given, outputs, {"HOW": how})
    log = tmp_path / "apt.log"
    ran["apt"] = log.read_text().splitlines() if log.exists() else []
    ran["confs"] = sorted(tmp_path.glob("apt.log.*.conf"))
    ran["dir"] = Path(plan["outputs"]["dir"])
    ran["slept"] = (tmp_path / "slept").read_text().split() if (tmp_path / "slept").exists() else []
    return ran


@then(parsers.re(r"the step (?P<how>passed|failed)(?: with (?P<code>\d+))?$"))
def step_result(ran, how, code):
    assert (ran["code"] == 0) == (how == "passed"), ran
    if code is not None:
        assert ran["code"] == int(code), ran


@then(parsers.re(r'apt-get ran (?P<runs>".*?")(?: alone)?$'))
def apt_ran(ran, runs):
    assert ran["apt"] == re.findall(r'"([^"]*)"', runs), ran


@then(parsers.re(r'it warned "(?P<warning>[^"]*)"$'))
def warned(ran, warning):
    assert f"::warning::{warning}" in ran["stdout"].splitlines(), ran


@then(parsers.re(r"it slept (?P<times>\d+) times? for (?P<seconds>\d+) seconds"))
def slept(ran, times, seconds):
    assert ran["slept"] == [seconds] * int(times), ran


@then(parsers.re(r"the step asked for (?P<how>a|no) save"))
def asked_save(ran, how):
    assert (ran["outputs"].get("save") == "true") == (how == "a"), ran


@then("the cache directory holds the lists and packages, owned by the runner, with no lock or partial download")
def cache_contents(ran):
    held = sorted(str(p.relative_to(ran["dir"])) for p in ran["dir"].rglob("*"))
    assert held == ["archives", "archives/qemu-user.deb", "lists", "lists/mirror_Packages"], held
    assert all(p.stat().st_uid == os.getuid() for p in ran["dir"].rglob("*"))


@then("every apt-get was given a configuration that keeps the lists and packages in the cache directory")
def every_conf(ran):
    assert len(ran["confs"]) == len(ran["apt"]) > 0, ran
    wanted = (f'Dir::State::Lists "{ran["dir"]}/lists";\nDir::Cache::Archives "{ran["dir"]}/archives";\n'
              'Dir::Cache::pkgcache "";\nDir::Cache::srcpkgcache "";\nAPT::Keep-Downloaded-Packages "true";\n')
    assert all(conf.read_text() == wanted for conf in ran["confs"]), [c.read_text() for c in ran["confs"]]


@when("I read the apt-install action", target_fixture="action")
def read_action():
    return _action()


def _cache_pin():
    text = (ACTIONS / "cache" / "action.yml").read_text()
    return set(re.findall(r"uses: (actions/cache/\w+@[0-9a-f]{40} # v[\d.]+)", text))


@then("its restore and save use actions/cache at the commit actions/cache uses, and continue on error")
def cache_steps(action):
    steps = {s.get("id") or s["name"]: s for s in action["runs"]["steps"]}
    restore, save = steps["restore"], steps["Save the apt packages"]
    text = (ACTION / "action.yml").read_text()
    pins = set(re.findall(r"uses: (actions/cache/\w+@[0-9a-f]{40} # v[\d.]+)", text))
    assert pins and pins <= _cache_pin(), pins
    assert restore["uses"].startswith("actions/cache/restore@") and save["uses"].startswith("actions/cache/save@")
    assert restore["continue-on-error"] is True and save["continue-on-error"] is True


@then("its restore takes the newest entry by the restore prefix, and its save keeps this run's key")
def rolling_keys(action):
    restore, save = [s for s in action["runs"]["steps"] if s.get("uses", "").startswith("actions/cache/")]
    assert restore["with"] == {"path": "${{ steps.plan.outputs.dir }}", "key": "${{ steps.plan.outputs.key }}",
                               "restore-keys": "${{ steps.plan.outputs.prefix }}"}
    assert save["with"] == {"path": "${{ steps.plan.outputs.dir }}", "key": "${{ steps.plan.outputs.key }}"}
    (install,) = [s for s in action["runs"]["steps"] if s.get("id") == "install"]
    assert install["env"]["MATCHED"] == "${{ steps.restore.outputs.cache-matched-key }}"


@then("its save runs only when the install step asks for one")
def save_condition(action):
    (save,) = [s for s in action["runs"]["steps"] if s.get("uses", "").startswith("actions/cache/save@")]
    assert save["if"] == "steps.install.outputs.save == 'true'"


@then(parsers.parse('every run step of the "{name}" action takes its inputs from its environment alone'))
def inputs_by_env(action, name):
    for step in action["runs"]["steps"]:
        if "run" in step:
            assert "${{" not in step["run"], f"{name}: {step['run']}"
            assert all(INPUT.match(v) or STEP.match(v) for v in step["env"].values()), f"{name}: {step['env']}"
