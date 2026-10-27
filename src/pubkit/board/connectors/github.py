"""The github connector: the GitHub REST API, for what a repository is
and holds (its visibility, pubkit.json, release.json, lock and workflow
files, rulesets, releases, open issues) and its security alerts.

It reads with one token, sent only to api.github.com. A refused or
missing read is a Fact with no value and GitHub's reason as its note.
"""

from __future__ import annotations

import base64
import binascii

from .base import Connector, Fact, Ledger, Transport

API = "https://api.github.com"
# Pages of 100 read for a count before it is reported as "at least".
PAGES = 10


def headers(token: str | None) -> dict[str, str]:
    out = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
           "User-Agent": "pubkit-board"}
    if token:
        out["Authorization"] = f"Bearer {token}"
    return out


class GitHub(Connector):
    name = "github"

    def __init__(self, ledger: Ledger, transport: Transport, token: str | None = None):
        super().__init__(ledger, transport, headers(token))

    def repository(self, repo: str) -> Fact:
        """Visibility, default branch and whether it is archived."""
        response, call = self.get("repository", f"{API}/repos/{repo}")
        if not response.ok:
            return Fact(None, (call,), response.message())
        data = response.json()
        return Fact({"visibility": data.get("visibility") or
                     ("private" if data.get("private") else "public"),
                     "default_branch": data.get("default_branch", "main"),
                     "archived": bool(data.get("archived"))}, (call,))

    def _file(self, method: str, repo: str, path: str) -> Fact:
        """A file's text on the default branch; None, noted, when absent."""
        response, call = self.get(method, f"{API}/repos/{repo}/contents/{path}")
        if response.status == 404:
            return Fact(None, (call,), f"no {path}")
        if not response.ok:
            return Fact(None, (call,), response.message())
        data = response.json()
        if not isinstance(data, dict) or data.get("type", "file") != "file" or "content" not in data:
            return Fact(None, (call,), f"{path} is not a file")
        try:
            return Fact(base64.b64decode(data["content"]).decode("utf-8"), (call,))
        except (binascii.Error, ValueError, UnicodeDecodeError) as e:
            return Fact(None, (call,), f"{path} could not be decoded ({e})")

    def pubkit_json(self, repo: str) -> Fact:
        return self._file("pubkit_json", repo, "pubkit.json")

    def release_json(self, repo: str) -> Fact:
        return self._file("release_json", repo, "release.json")

    def requirements_lock(self, repo: str) -> Fact:
        return self._file("requirements_lock", repo, "tests/requirements.lock")

    def workflow(self, repo: str, name: str) -> Fact:
        return self._file("workflow", repo, f".github/workflows/{name}")

    def workflow_names(self, repo: str) -> Fact:
        """The file names in .github/workflows."""
        response, call = self.get("workflow_names", f"{API}/repos/{repo}/contents/.github/workflows")
        if response.status == 404:
            return Fact([], (call,), "no .github/workflows")
        if not response.ok:
            return Fact(None, (call,), response.message())
        listed = response.json()
        return Fact(sorted(f["name"] for f in listed if isinstance(f, dict)
                           and f.get("type") == "file"), (call,))

    def rulesets(self, repo: str) -> Fact:
        """The repository's own rulesets, each in full."""
        response, call = self.get("rulesets",
                                  f"{API}/repos/{repo}/rulesets?includes_parents=false")
        if not response.ok:
            return Fact(None, (call,), response.message())
        calls, out = [call], []
        for listed in response.json() or []:
            one, one_call = self.get("ruleset", f"{API}/repos/{repo}/rulesets/{listed['id']}")
            calls.append(one_call)
            if not one.ok:
                return Fact(None, tuple(calls), one.message())
            out.append(one.json())
        return Fact(out, tuple(calls))

    def latest_release(self, repo: str) -> Fact:
        """The newest published release: its tag and when it was published."""
        response, call = self.get("latest_release", f"{API}/repos/{repo}/releases?per_page=10")
        if not response.ok:
            return Fact(None, (call,), response.message())
        published = [r for r in response.json() or [] if not r.get("draft") and r.get("published_at")]
        if not published:
            return Fact(None, (call,), "GitHub has no published release")
        newest = max(published, key=lambda r: r["published_at"])
        return Fact({"tag": newest["tag_name"], "at": newest["published_at"]}, (call,))

    def _count(self, method: str, url: str, keep=lambda item: True) -> Fact:
        """How many items a listing has, page by page."""
        calls, total = [], 0
        for page in range(1, PAGES + 1):
            response, call = self.get(method, f"{url}&per_page=100&page={page}")
            calls.append(call)
            if not response.ok:
                return Fact(None, tuple(calls), response.message())
            items = response.json() or []
            total += sum(1 for item in items if keep(item))
            if len(items) < 100:
                return Fact(total, tuple(calls))
        return Fact(total, tuple(calls), f"at least {total}: stopped after {PAGES} pages")

    def open_issues(self, repo: str) -> Fact:
        """Open issues; pull requests, which GitHub lists with them, are not counted."""
        return self._count("open_issues", f"{API}/repos/{repo}/issues?state=open",
                           lambda item: "pull_request" not in item)

    def dependabot_alerts(self, repo: str) -> Fact:
        return self._count("dependabot_alerts", f"{API}/repos/{repo}/dependabot/alerts?state=open")

    def code_scanning_alerts(self, repo: str) -> Fact:
        return self._count("code_scanning_alerts",
                           f"{API}/repos/{repo}/code-scanning/alerts?state=open")

    def secret_scanning_alerts(self, repo: str) -> Fact:
        return self._count("secret_scanning_alerts",
                           f"{API}/repos/{repo}/secret-scanning/alerts?state=open")
