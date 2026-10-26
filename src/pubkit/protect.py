"""`pubkit protect`: pubkit's rulesets on every repository: one on the
default branch, and one on the release tags.

`ruleset.json` is the ruleset every pub* repository's main carries: no
deletion, no force push, changes only by pull request (no approval needed,
since one person owns them), only after the required checks pass, and
nobody may bypass it, and it ships with the wheel. Which repositories carry
it, and the checks each requires (all that differs between them), is local
configuration: a `protected.json` in the working directory, or one named
with `--config`, or the repositories and `--require CHECK` on the command
line. pubkit's own list is the protected.json in its checkout.

`tag-ruleset.json`, also in the wheel, keeps the release tags (`v*`) to
CI: nobody may create, move or delete one, and only GitHub Actions, where
`pubrel tag` runs once a release pull request merges, may bypass it. A
hand-pushed tag cannot release code that CI did not pass. A repository
that releases no tags goes without it: `"tags": false` in its entry, or
`--no-tags`.

`pubkit protect [REPO...]` creates each ruleset in each repository, or
updates the one already on its default branch or its release tags,
renaming it, so a repository never carries two. It calls the GitHub API through `gh`, which
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

LOCAL = "protected.json"
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def base(name: str = "ruleset.json") -> dict:
    """One of pubkit's rulesets; the default branch's is without its checks."""
    return json.loads(resources.files("pubkit").joinpath(name).read_text(encoding="utf-8"))


def tag_ruleset() -> dict:
    """The ruleset every repository's release tags carry."""
    return base("tag-ruleset.json")


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


def on_release_tags(rs: dict) -> bool:
    include = rs.get("conditions", {}).get("ref_name", {}).get("include", [])
    return rs.get("target") == "tag" and bool({"~ALL", "refs/tags/v*"} & set(include))


def current(api: Api, repo: str, target: str = "branch") -> list[dict]:
    """The repository's own rulesets that govern its default branch, or
    with target "tag" its release tags."""
    listed = api("GET", f"repos/{repo}/rulesets?includes_parents=false", None) or []
    full = [api("GET", f"repos/{repo}/rulesets/{r['id']}", None)
            for r in listed if r.get("target") == target]
    return [r for r in full if (on_default_branch if target == "branch" else on_release_tags)(r)]


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


def protect(api: Api, repo: str, checks: list[str], check: bool,
            tags: bool = True) -> tuple[list[str], bool]:
    """Bring one repository to both rulesets (with `tags` false, only its
    default branch's); its report lines, and whether it differed (with
    `check`) or could not be brought in line."""
    lines, bad = attempt(lambda: bring(api, repo, ruleset(checks), current(api, repo),
                                       "its default branch", check), repo, check)
    if not tags:
        return lines, bad
    tag_lines, tag_bad = attempt(lambda: bring(api, repo, tag_ruleset(), current(api, repo, "tag"),
                                               "its release tags", check), repo, check)
    return lines + tag_lines, bad or tag_bad


def attempt(step, repo: str, check: bool) -> tuple[list[str], bool]:
    """Run one ruleset's step, reporting GitHub's refusal as a failed line,
    so the other ruleset's report is not lost."""
    try:
        return step()
    except ApiError as e:
        lines = [f"{repo}: {e}"]
        if not check and e.status in (403, 404):
            lines.append("  changing a ruleset needs a repository admin: "
                         "`gh auth login` as its owner, then run this again")
        return lines, True


def bring(api: Api, repo: str, want: dict, have: list[dict], where: str,
          check: bool) -> tuple[list[str], bool]:
    """Bring the rulesets on `where` to `want`, as protect does."""
    if not have:
        if check:
            return [f"{repo}: no ruleset on {where}"], True
        api("POST", f"repos/{repo}/rulesets", want)
        return [f"{repo}: created \"{want['name']}\""], False
    named = [r for r in have if r.get("name") == want["name"]]
    target = (named or have)[0]
    others = [r for r in have if r is not target]
    lines = [f"{repo}: also on {where}, \"{r['name']}\" (id {r['id']}): "
             f"delete it in Settings, Rules, Rulesets" for r in others]
    diff = differences(want, target)
    if not diff:
        return [f"{repo}: \"{want['name']}\" up to date", *lines], bool(others)
    if check:
        return [f"{repo}: differs from pubkit's \"{want['name']}\"", *(f"  {d}" for d in diff),
                *lines], True
    api("PUT", f"repos/{repo}/rulesets/{target['id']}", want)
    return [f"{repo}: updated \"{target['name']}\" (id {target['id']})",
            *(f"  was: {d}" for d in diff), *lines], bool(others)


def load(config: Path | None) -> list[dict]:
    """The repositories and their checks: `config`, else the protected.json
    in the working directory."""
    if config is None:
        if not Path(LOCAL).is_file():
            sys.exit(f"pubkit protect: no ./{LOCAL}; pass --config FILE, or name the "
                     f"repositories with --require CHECK for each check they require")
        config = Path(LOCAL)
    elif not config.is_file():
        sys.exit(f"pubkit protect: no {config}")
    entries = json.loads(config.read_text(encoding="utf-8"))["repositories"]
    for e in entries:
        if not REPO.fullmatch(e.get("repo", "")) or not e.get("checks"):
            sys.exit(f"pubkit protect: protected.json: {json.dumps(e)} needs a repo, "
                     f"OWNER/NAME, and the checks it requires")
    return entries


def run(config: Path | None, repos: list[str], check: bool, requires: list[str] | None = None,
        api: Api = gh, tags: bool = True) -> int:
    """Protect `repos` (by default every one listed). Given `requires`, the
    named repositories require those checks, and no list is read. With
    `tags` false, no repository gets the tag ruleset."""
    if requires:
        if not repos:
            sys.exit("pubkit protect: --require names a check; name the repositories too")
        bad = [r for r in repos if not REPO.fullmatch(r)]
        if bad:
            sys.exit(f"pubkit protect: {', '.join(bad)} is not OWNER/NAME")
        entries = [{"repo": r, "checks": requires} for r in repos]
    else:
        entries = load(config)
    known = {e["repo"]: e for e in entries}
    unknown = [r for r in repos if r not in known]
    if unknown:
        sys.exit(f"pubkit protect: {', '.join(unknown)} not in protected.json; add "
                 f'{{"repo": "{unknown[0]}", "checks": [...]}} to it first')
    failed = False
    for entry in [known[r] for r in repos] or entries:
        lines, bad = protect(api, entry["repo"], entry["checks"], check,
                             tags and entry.get("tags", True))
        print("\n".join(lines))
        failed = failed or bad
    return 1 if failed else 0

