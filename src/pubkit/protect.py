"""`pubkit protect`: one ruleset on the default branch of every repository.

`ruleset.json` is the ruleset every pub* repository's main carries: no
deletion, no force push, changes only by pull request (no approval needed,
since one person owns them), only after the required checks pass, and
nobody may bypass it. `protected.json`, shipped beside it, lists the
repositories and the checks each one requires, which is all that differs
between them. Both come with the wheel, so an installed pubkit protects
the repositories its release names, from any directory.

`pubkit protect [REPO...]` creates the ruleset in each repository, or
updates the one already on its default branch, renaming it, so a
repository never carries two. It calls the GitHub API through `gh`, which
must be signed in as someone who administers the repositories.
`pubkit protect --check` changes nothing: it prints how each repository
differs, and fails when one does. `--config FILE` reads another list, such
as an edited one not yet released.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Any

# The app that reports a check. Every other check is a GitHub Actions job.
GITHUB_ACTIONS = 15368
APPS = {"SonarCloud Code Analysis": 12526}

Api = Callable[[str, str, Any], Any]

REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def base() -> dict:
    """pubkit's ruleset, without the checks."""
    return json.loads(resources.files("pubkit").joinpath("ruleset.json").read_text(encoding="utf-8"))


def ruleset(checks: list[str]) -> dict:
    """The ruleset a repository requiring `checks` carries."""
    out = base()
    for rule in out["rules"]:
        if rule["type"] == "required_status_checks":
            rule["parameters"]["required_status_checks"] = [
                {"context": c, "integration_id": APPS.get(c, GITHUB_ACTIONS)} for c in checks]
    return out


def gh(method: str, path: str, body: Any = None) -> Any:
    """One GitHub API call through `gh api`; its JSON, or None when empty."""
    argv = ["gh", "api", "-X", method, "-H", "Accept: application/vnd.github+json", path]
    if body is not None:
        argv += ["--input", "-"]
    proc = subprocess.run(argv, input=None if body is None else json.dumps(body),
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise ApiError(f"gh api -X {method} {path}: {proc.stderr.strip()}", proc.stdout)
    return json.loads(proc.stdout) if proc.stdout.strip() else None


class ApiError(Exception):
    """A refused call: gh's message, then the reasons GitHub's response gives."""

    def __init__(self, message: str, response: str = ""):
        m = re.search(r"\(HTTP (\d+)\)", message)
        self.status = int(m.group(1)) if m else None
        try:
            errors = json.loads(response).get("errors") or []
        except (ValueError, AttributeError):
            errors = []
        reasons = [e if isinstance(e, str) else e.get("message") or json.dumps(e) for e in errors]
        super().__init__("; ".join([message, *reasons]))


def on_default_branch(rs: dict) -> bool:
    include = rs.get("conditions", {}).get("ref_name", {}).get("include", [])
    return rs.get("target") == "branch" and bool(
        {"~DEFAULT_BRANCH", "~ALL", "refs/heads/main"} & set(include))


def current(api: Api, repo: str) -> list[dict]:
    """The repository's own rulesets that govern its default branch."""
    listed = api("GET", f"repos/{repo}/rulesets?includes_parents=false", None) or []
    full = [api("GET", f"repos/{repo}/rulesets/{r['id']}", None)
            for r in listed if r.get("target") == "branch"]
    return [r for r in full if on_default_branch(r)]


def _rules(rs: dict) -> dict[str, dict]:
    return {r["type"]: r.get("parameters", {}) for r in rs.get("rules", [])}


def _checks(params: dict) -> set[tuple[str, int | None]]:
    return {(c["context"], c.get("integration_id"))
            for c in params.get("required_status_checks", [])}


def differences(want: dict, have: dict) -> list[str]:
    """How `have` falls short of, or goes beyond, `want`: one line each.
    Only what `want` sets is compared, so a setting GitHub adds with its own
    default is no difference."""
    out = []
    for key in ("name", "target", "enforcement", "conditions", "bypass_actors"):
        if want[key] != have.get(key):
            out.append(f"{key} is {json.dumps(have.get(key))}, not {json.dumps(want[key])}")
    wanted, had = _rules(want), _rules(have)
    for kind in wanted.keys() - had.keys():
        out.append(f"no {kind} rule")
    for kind in had.keys() - wanted.keys():
        out.append(f"an extra {kind} rule")
    for kind in wanted.keys() & had.keys():
        w, h = wanted[kind], had[kind]
        for key in w:
            if key == "required_status_checks":
                for context, app in sorted(_checks(w) - _checks(h), key=str):
                    out.append(f"does not require {context} (app {app})")
                for context, app in sorted(_checks(h) - _checks(w), key=str):
                    out.append(f"also requires {context} (app {app})")
            elif key == "allowed_merge_methods":
                if set(w[key]) != set(h.get(key, [])):
                    out.append(f"{kind}.{key} is {h.get(key)}, not {w[key]}")
            elif w[key] != h.get(key):
                out.append(f"{kind}.{key} is {json.dumps(h.get(key))}, not {json.dumps(w[key])}")
    return out


def protect(api: Api, repo: str, checks: list[str], check: bool) -> tuple[list[str], bool]:
    """Bring one repository to the ruleset; its report lines, and whether
    it differed (with `check`) or could not be brought in line."""
    want = ruleset(checks)
    have = current(api, repo)
    if not have:
        if check:
            return [f"{repo}: no ruleset on its default branch"], True
        api("POST", f"repos/{repo}/rulesets", want)
        return [f"{repo}: created \"{want['name']}\""], False
    named = [r for r in have if r.get("name") == want["name"]]
    target = (named or have)[0]
    others = [r for r in have if r is not target]
    lines = [f"{repo}: also on its default branch, \"{r['name']}\" (id {r['id']}): "
             f"delete it in Settings, Rules, Rulesets" for r in others]
    diff = differences(want, target)
    if not diff:
        return [f"{repo}: up to date", *lines], bool(others)
    if check:
        return [f"{repo}: differs from pubkit's ruleset", *(f"  {d}" for d in diff), *lines], True
    api("PUT", f"repos/{repo}/rulesets/{target['id']}", want)
    return [f"{repo}: updated \"{target['name']}\" (id {target['id']})",
            *(f"  was: {d}" for d in diff), *lines], bool(others)


def load(config: Path | None) -> list[dict]:
    """The repositories and their checks: `config`, or the list this pubkit ships."""
    if config is None:
        text = resources.files("pubkit").joinpath("protected.json").read_text(encoding="utf-8")
    elif not config.is_file():
        sys.exit(f"pubkit protect: no {config}")
    else:
        text = config.read_text(encoding="utf-8")
    entries = json.loads(text)["repositories"]
    for e in entries:
        if not REPO.fullmatch(e.get("repo", "")) or not e.get("checks"):
            sys.exit(f"pubkit protect: protected.json: {json.dumps(e)} needs a repo, "
                     f"OWNER/NAME, and the checks it requires")
    return entries


def run(config: Path | None, repos: list[str], check: bool, api: Api = gh) -> int:
    entries = load(config)
    known = {e["repo"]: e for e in entries}
    unknown = [r for r in repos if r not in known]
    if unknown:
        sys.exit(f"pubkit protect: {', '.join(unknown)} not in protected.json; add "
                 f'{{"repo": "{unknown[0]}", "checks": [...]}} to it first')
    failed = False
    for entry in [known[r] for r in repos] or entries:
        try:
            lines, bad = protect(api, entry["repo"], entry["checks"], check)
        except ApiError as e:
            lines, bad = [f"{entry['repo']}: {e}"], True
            if not check and e.status in (403, 404):
                lines.append("  changing a ruleset needs a repository admin: "
                             "`gh auth login` as its owner, then run this again")
        print("\n".join(lines))
        failed = failed or bad
    return 1 if failed else 0

