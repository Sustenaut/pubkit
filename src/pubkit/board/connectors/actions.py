"""The actions connector: the GitHub Actions API, for what CI did. The
runs and job results on main, a repository's own nightly runs, and the
live-test and deploy runs a service declares.

It shares the github connector's token, but answers a different
question: not what a repository holds, but how its workflows went.
"""

from __future__ import annotations

from .base import Connector, Fact, Ledger, Transport
from .github import API, headers


def run(item: dict) -> dict:
    return {"id": item.get("id"), "workflow": (item.get("path") or "").rsplit("/", 1)[-1],
            "event": item.get("event"), "status": item.get("status"),
            "conclusion": item.get("conclusion"), "at": item.get("updated_at") or item.get("created_at"),
            "url": item.get("html_url")}


class Actions(Connector):
    name = "actions"

    def __init__(self, ledger: Ledger, transport: Transport, token: str | None = None):
        super().__init__(ledger, transport, headers(token))

    def main_runs(self, repo: str, branch: str = "main") -> Fact:
        """The latest completed runs pushes to the default branch started, newest first."""
        response, call = self.get(
            "main_runs",
            f"{API}/repos/{repo}/actions/runs?branch={branch}&event=push&status=completed&per_page=30")
        if not response.ok:
            return Fact(None, (call,), response.message())
        return Fact([run(r) for r in (response.json() or {}).get("workflow_runs", [])], (call,))

    def jobs(self, repo: str, run_id: int) -> Fact:
        """Each job of a run, by name, with its conclusion."""
        response, call = self.get("jobs",
                                  f"{API}/repos/{repo}/actions/runs/{run_id}/jobs?per_page=100")
        if not response.ok:
            return Fact(None, (call,), response.message())
        return Fact([{"name": j.get("name"), "conclusion": j.get("conclusion")}
                     for j in (response.json() or {}).get("jobs", [])], (call,))

    def _workflow_runs(self, method: str, repo: str, workflow: str) -> Fact:
        response, call = self.get(
            method, f"{API}/repos/{repo}/actions/workflows/{workflow}/runs?status=completed&per_page=10")
        if response.status == 404:
            return Fact([], (call,), f"no workflow {workflow}")
        if not response.ok:
            return Fact(None, (call,), response.message())
        return Fact([run(r) for r in (response.json() or {}).get("workflow_runs", [])], (call,))

    def nightly_runs(self, repo: str, workflow: str = "nightly.yml") -> Fact:
        """The repository's own nightly workflow's latest completed runs."""
        return self._workflow_runs("nightly_runs", repo, workflow)

    def live_test_runs(self, repo: str, workflow: str) -> Fact:
        """The latest completed runs of the workflow that tests the live service."""
        return self._workflow_runs("live_test_runs", repo, workflow)

    def deploy_runs(self, repo: str, workflow: str) -> Fact:
        """The latest completed runs of the workflow that deploys to production."""
        return self._workflow_runs("deploy_runs", repo, workflow)
