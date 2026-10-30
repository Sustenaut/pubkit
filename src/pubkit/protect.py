"""`pubkit protect`: pubkit's rulesets on every repository: one on the
default branch, and one on the release tags.

`ruleset.json` is the ruleset every pub* repository's main carries: no
deletion, no force push, changes only by pull request (no approval needed,
since one person owns them), merged by GitHub's merge queue once the
required checks pass on the queue's merge of the pull request into main,
and nobody may bypass it, and it ships with the wheel. The queue tests the
tree main will hold, so a pull request need not be up to date with main.
GitHub offers a merge queue only to an organization's public repositories
(and Enterprise Cloud's private ones), so a repository without one, `"queue":
false` in its entry or `--no-queue`, goes without the queue and instead
merges only after the checks pass on a branch up to date with main. pubkit
reads each repository's owner and visibility (and, for a private one, its
organization's plan) and leaves the queue out by itself where GitHub has
none, saying so; should GitHub still refuse the merge_queue rule, it tries
once more without it. Either way, what merges was tested on the latest main. Which
repositories carry it, and the checks each requires, is local
configuration: a `protected.json` in the working directory, or one named
with `--config`, or the repositories and `--require CHECK` on the command
line. pubkit's own list is the protected.json in its checkout.

`tag-ruleset.json`, also in the wheel, keeps the release tags (`v*`) to
CI: nobody may create, move or delete one, and only GitHub Actions, where
`pubrel tag` runs once a release pull request merges, may bypass it. In
an organization's repository GitHub refuses the GitHub Actions app as a
bypass actor, so an organization's repository names its release app with
`"release_app": ID` in its entry, and its tags let that app through instead,
whose token `pubrel tag` uses there. A repository of RELEASE_APP_OWNER that
names none still gets RELEASE_APP, with a warning, until it does. A
hand-pushed tag cannot release code that CI did not pass. A repository
that releases no tags goes without it: `"tags": false` in its entry, or
`--no-tags`.

A repository may instead say how it is protected in its own pubkit.json:
`"protect": {"checks": [...], "tags": false, "queue": false, "auto_merge": true,
"release_app": 12345}` (`tags` and `queue` default to true). `auto_merge` is the repository's
"Allow auto-merge" setting, which lets a pull request merge by itself once
the ruleset's checks pass: true turns it on, false off, and without it
protect leaves the setting as it is. It loosens nothing, since the ruleset
still decides what may merge. A protected.json entry takes it too.
`pubkit protect OWNER/NAME` reads it from GitHub for a repository the list
does not name, and `pubkit protect` with no list and no repositories reads
`./pubkit.json`, for the repository `origin` points at.

`pubkit protect [REPO...]` creates each ruleset in each repository, or
updates the one already on its default branch or its release tags,
renaming it, so a repository never carries two. It calls the GitHub API through `gh`, which
must be signed in as someone who administers the repositories.
`pubkit protect --check` changes nothing: it prints how each repository
differs, and fails when one does. `--config FILE` reads another list, such
as an edited one not yet released. `--repos FILE` reads a repos file instead
(repos.py): the repositories it marks to protect, each with the checks its
own pubkit.json says. protected.json and `--config` go in a later release.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Any

from .repos import REPO, read, warn_lists

# The app that reports a check. Every other check is a GitHub Actions job.
GITHUB_ACTIONS = 15368
# The release app a repository of this owner gets when its entry names no
# `release_app`, with a warning, until every one of them does.
RELEASE_APP = 5529889
RELEASE_APP_OWNER = "sustenaut"
APPS = {"SonarCloud Code Analysis": 12526}

Api = Callable[[str, str, Any], Any]

LOCAL = "protected.json"
OWN = "pubkit.json"


def base(name: str = "ruleset.json") -> dict:
    """One of pubkit's rulesets; the default branch's is without its checks."""
    return json.loads(resources.files("pubkit").joinpath(name).read_text(encoding="utf-8"))


def falls_back(repo: str, release_app: int | None) -> bool:
    """Whether `repo` gets RELEASE_APP because its entry names no release app."""
    return release_app is None and repo.split("/", 1)[0].lower() == RELEASE_APP_OWNER


def tag_ruleset(repo: str = "", release_app: int | None = None) -> dict:
    """The ruleset `repo`'s release tags carry: GitHub Actions bypasses it,
    or the release app its entry names."""
    out = base("tag-ruleset.json")
    if falls_back(repo, release_app):
        release_app = RELEASE_APP
    if release_app is not None:
        out["bypass_actors"] = [{"actor_id": release_app, "actor_type": "Integration",
                                 "bypass_mode": "always"}]
    return out


def ruleset(checks: list[str], queue: bool = True) -> dict:
    """The ruleset a repository requiring `checks` carries: through the
    merge queue, or with `queue` false, from a branch up to date with main."""
    out = base()
    if not queue:
        out["rules"] = [r for r in out["rules"] if r["type"] != "merge_queue"]
    for rule in out["rules"]:
        if rule["type"] == "required_status_checks":
            rule["parameters"]["required_status_checks"] = [
                {"context": c, "integration_id": APPS.get(c, GITHUB_ACTIONS)} for c in checks]
            rule["parameters"]["strict_required_status_checks_policy"] = not queue
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


NO_QUEUE = ("main merges only after its required checks pass on a branch up to date "
            "with main")


def no_queue(api: Api, repo: str) -> str | None:
    """Why GitHub offers `repo` no merge queue, or None when it does, or
    when that cannot be read (GitHub's answer to the ruleset then says)."""
    try:
        info = api("GET", f"repos/{repo}", None) or {}
    except ApiError:
        return None
    if info.get("owner", {}).get("type", "Organization") != "Organization":
        return "GitHub offers a merge queue only to an organization's repositories"
    visibility = info.get("visibility", "public")
    if visibility == "public":
        return None
    owner = repo.split("/", 1)[0]
    try:
        plan = ((api("GET", f"orgs/{owner}", None) or {}).get("plan") or {}).get("name", "")
    except ApiError:
        plan = ""
    if plan == "enterprise":
        return None
    return (f"GitHub offers a merge queue to a {visibility} repository only on Enterprise "
            f"Cloud" + (f", and {owner} is on {plan}" if plan else ""))


def protect(api: Api, repo: str, checks: list[str], check: bool,
            tags: bool = True, queue: bool = True,
            auto_merge: bool | None = None,
            release_app: int | None = None) -> tuple[list[str], bool]:
    """Bring one repository to both rulesets (with `tags` false, only its
    default branch's; with `queue` false, or where GitHub offers none,
    main's without the merge queue; with `release_app`, its tags' letting
    that app through), and, unless `auto_merge` is None, its "Allow
    auto-merge" setting to `auto_merge`; its report lines, and whether it
    differed (with `check`) or could not be brought in line."""
    notes = []
    if tags and falls_back(repo, release_app):
        notes.append(f"{repo}: warning: no release_app in its protect block, so its tags let "
                     f"through app {RELEASE_APP}; add \"release_app\": {RELEASE_APP} to its "
                     f"pubkit.json")
    if queue and (why := no_queue(api, repo)):
        queue = False
        notes.append(f"{repo}: no merge queue: {why}; {NO_QUEUE}")

    def branch() -> tuple[list[str], bool]:
        try:
            return bring(api, repo, ruleset(checks, queue), current(api, repo),
                         "its default branch", check)
        except ApiError as e:
            if not queue or e.status != 422 or "merge_queue" not in str(e):
                raise
        lines, bad = bring(api, repo, ruleset(checks, False), current(api, repo),
                           "its default branch", check)
        return [f"{repo}: warning: GitHub refused the merge_queue rule, so main goes without "
                f"the queue; {NO_QUEUE}", *lines], bad

    lines, bad = attempt(branch, repo, check)
    lines = notes + lines
    if tags:
        tag_lines, tag_bad = attempt(lambda: bring(api, repo, tag_ruleset(repo, release_app),
                                                   current(api, repo, "tag"),
                                                   "its release tags", check), repo, check)
        lines, bad = lines + tag_lines, bad or tag_bad
    if auto_merge is not None:
        merge_lines, merge_bad = attempt(lambda: allow_auto_merge(api, repo, auto_merge, check),
                                         repo, check)
        lines, bad = lines + merge_lines, bad or merge_bad
    return lines, bad


def allow_auto_merge(api: Api, repo: str, want: bool, check: bool) -> tuple[list[str], bool]:
    """Bring the repository's "Allow auto-merge" setting to `want`."""
    def said(on: bool) -> str:
        return "allowed" if on else "not allowed"
    have = (api("GET", f"repos/{repo}", None) or {}).get("allow_auto_merge")
    if have is None and check:
        return [f"{repo}: auto-merge not readable; reading it needs push access"], True
    if have == want:
        return [f"{repo}: auto-merge {said(want)}, up to date"], False
    if check:
        return [f"{repo}: auto-merge is {said(bool(have))}; pubkit wants it {said(want)}"], True
    api("PATCH", f"repos/{repo}", {"allow_auto_merge": want})
    return [f"{repo}: auto-merge now {said(want)}"], False


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


KEYS = {"checks", "tags", "queue", "auto_merge", "release_app"}


def problems(block: Any) -> list[str]:
    """What is wrong with a protect block or a protected.json entry's checks
    and tags: one phrase each, none when it is sound."""
    if not isinstance(block, dict):
        return [f"it is {json.dumps(block)}, not an object"]
    out = []
    checks = block.get("checks")
    if not isinstance(checks, list) or not checks:
        out.append('"checks" must list at least one check')
    else:
        if not all(isinstance(c, str) and c.strip() == c and c for c in checks):
            out.append('every check must be a name, a non-empty string without '
                       'surrounding spaces')
        elif len(set(checks)) != len(checks):
            out.append('"checks" lists a check twice')
    for key in ("tags", "queue", "auto_merge"):
        if not isinstance(block.get(key, True), bool):
            out.append(f'"{key}" must be true or false, not {json.dumps(block[key])}')
    app = block.get("release_app", 1)
    if isinstance(app, bool) or not isinstance(app, int) or app <= 0:
        out.append(f'"release_app" must be a GitHub App\'s id, a positive integer, '
                   f'not {json.dumps(app)}')
    return out


def entry(repo: str, config: Any, where: str) -> dict:
    """A repository's entry from its pubkit.json's protect block."""
    if not isinstance(config, dict) or "protect" not in config:
        raise ValueError(f"{where} has no protect block; add "
                         f'"protect": {{"checks": ["ci / passed"]}} to it')
    block = config["protect"]
    bad = problems(block)
    if isinstance(block, dict) and block.keys() - KEYS:
        bad.append(f"{', '.join(sorted(block.keys() - KEYS))} is not a protect setting; "
                   f"it takes checks, tags, queue, auto_merge and release_app")
    if bad:
        raise ValueError(f"{where}'s protect block: {'; '.join(bad)}")
    return {"repo": repo, "checks": block["checks"], "tags": block.get("tags", True),
            "queue": block.get("queue", True), "auto_merge": block.get("auto_merge"),
            "release_app": block.get("release_app")}


def parse(text: str | bytes, where: str) -> Any:
    """A pubkit.json's contents, or a ValueError saying it is not JSON."""
    try:
        return json.loads(text)
    except (ValueError, UnicodeDecodeError) as e:
        raise ValueError(f"{where} is not JSON ({e})") from e


def remote(api: Api, repo: str) -> dict:
    """`repo`'s entry, from the pubkit.json on its default branch."""
    try:
        found = api("GET", f"repos/{repo}/contents/{OWN}", None)
    except ApiError as e:
        raise ValueError(f"no {OWN} on its default branch ({e})") from e
    if not isinstance(found, dict) or found.get("type", "file") != "file" \
            or found.get("encoding", "base64") != "base64" or not found.get("content"):
        raise ValueError(f"GitHub did not return its {OWN} as a file")
    try:
        text = base64.b64decode(found["content"])
    except ValueError as e:
        raise ValueError(f"GitHub returned its {OWN} undecodable ({e})") from e
    return entry(repo, parse(text, f"its {OWN}"), f"its {OWN}")


def origin() -> str:
    """OWNER/NAME of the GitHub repository `origin` points at."""
    proc = subprocess.run(["git", "remote", "get-url", "origin"],
                          capture_output=True, text=True, check=False)
    m = re.search(r"github\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$",
                  proc.stdout.strip())
    if not m:
        sys.exit("pubkit protect: origin is not a GitHub repository; name it as OWNER/NAME")
    return m.group(1)


def load(config: Path | None) -> list[dict] | None:
    """The repositories and their checks: `config`, else the protected.json
    in the working directory; None when there is neither."""
    if config is None:
        if not Path(LOCAL).is_file():
            return None
        config = Path(LOCAL)
    elif not config.is_file():
        sys.exit(f"pubkit protect: no {config}")
    warn_lists("protect", f"{config} and --config")
    try:
        entries = parse(config.read_text(encoding="utf-8"), str(config))["repositories"]
    except (ValueError, KeyError, TypeError) as e:
        sys.exit(f"pubkit protect: {config} needs a \"repositories\" list ({e})")
    if not isinstance(entries, list):
        sys.exit(f"pubkit protect: {config}'s \"repositories\" is not a list")
    for e in entries:
        if not isinstance(e, dict) or not REPO.fullmatch(str(e.get("repo", ""))) \
                or not e.get("checks"):
            sys.exit(f"pubkit protect: protected.json: {json.dumps(e)} needs a repo, "
                     f"OWNER/NAME, and the checks it requires")
        bad = problems(e)
        if bad:
            sys.exit(f"pubkit protect: protected.json: {e['repo']}: {'; '.join(bad)}")
    repos = [e["repo"] for e in entries]
    twice = sorted({r for r in repos if repos.count(r) > 1})
    if twice:
        sys.exit(f"pubkit protect: protected.json lists {', '.join(twice)} more than once")
    return entries


def run(config: Path | None, repos: list[str], check: bool, requires: list[str] | None = None,
        api: Api = gh, tags: bool = True, queue: bool = True,
        repos_file: Path | None = None) -> int:
    """Protect `repos` (by default every one listed). Given `requires`, the
    named repositories require those checks, and no list is read. Given
    `repos_file`, the repositories it marks to protect are read from their
    own pubkit.json. With `tags` false, no repository gets the tag ruleset;
    with `queue` false, none gets the merge queue."""
    if repos_file:
        repos = repos or [e["repo"] for e in read(repos_file, "protect") if e["protect"]]
        entries = []
    elif requires:
        if not repos:
            sys.exit("pubkit protect: --require names a check; name the repositories too")
        bad = [r for r in repos if not REPO.fullmatch(r)]
        if bad:
            sys.exit(f"pubkit protect: {', '.join(bad)} is not OWNER/NAME")
        entries = [{"repo": r, "checks": requires} for r in repos]
    else:
        bad = [r for r in repos if not REPO.fullmatch(r)]
        if bad:
            sys.exit(f"pubkit protect: {', '.join(bad)} is not OWNER/NAME")
        entries = load(config)
        if entries is None and not repos:
            if not Path(OWN).is_file():
                sys.exit(f"pubkit protect: no ./{LOCAL} or ./{OWN}; pass --config FILE, or "
                         f"name the repositories with --require CHECK for each check they "
                         f"require")
            own = origin()
            try:
                entries = [entry(own, parse(Path(OWN).read_text(encoding="utf-8"), f"./{OWN}"),
                                 f"./{OWN}")]
            except ValueError as e:
                sys.exit(f"pubkit protect: {e}")
    known = {e["repo"]: e for e in entries or []}
    failed = False
    for name in repos or list(known):
        if name in known:
            item = known[name]
        else:
            try:
                item = remote(api, name)
            except ValueError as e:
                print(f"{name}: {e}" if repos_file else f"{name}: not in {LOCAL}, and {e}")
                failed = True
                continue
        lines, bad = protect(api, item["repo"], item["checks"], check,
                             tags and item.get("tags", True), queue and item.get("queue", True),
                             item.get("auto_merge"), item.get("release_app"))
        print("\n".join(lines))
        failed = failed or bad
    return 1 if failed else 0

