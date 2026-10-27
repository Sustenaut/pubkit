"""The sonarcloud connector: SonarQube Cloud's Web API, for a project's
reliability, maintainability and security ratings and its quality gate.

pubkit's CI names a repository's project OWNER_REPO. A public project
needs no token; SONAR_TOKEN, when set, reads a private one.
"""

from __future__ import annotations

from .base import Connector, Fact, Ledger, Transport

API = "https://sonarcloud.io/api"
METRICS = {"reliability_rating": "reliability", "sqale_rating": "maintainability",
           "security_rating": "security", "alert_status": "gate"}
LETTERS = "ABCDE"


def project(repo: str) -> str:
    return repo.replace("/", "_")


class SonarCloud(Connector):
    name = "sonarcloud"

    def __init__(self, ledger: Ledger, transport: Transport, token: str | None = None):
        super().__init__(ledger, transport,
                         {"Authorization": f"Bearer {token}"} if token else {})

    def ratings(self, repo: str) -> Fact:
        """Each rating as a letter, A to E, and the gate, OK or ERROR;
        None when SonarQube Cloud has no analysis of the project."""
        response, call = self.get(
            "ratings", f"{API}/measures/component?component={project(repo)}"
                       f"&metricKeys={','.join(METRICS)}")
        if response.status == 404:
            return Fact(None, (call,), f"no SonarQube Cloud project {project(repo)}")
        if not response.ok:
            return Fact(None, (call,), response.message())
        measures = {m["metric"]: m.get("value")
                    for m in (response.json() or {}).get("component", {}).get("measures", [])}
        out = {}
        for metric, name in METRICS.items():
            value = measures.get(metric)
            if value is None:
                continue
            if metric == "alert_status":
                out[name] = value
            else:
                out[name] = LETTERS[min(max(round(float(value)), 1), 5) - 1]
        if not {"reliability", "maintainability", "security"} <= out.keys():
            return Fact(None, (call,), f"SonarQube Cloud project {project(repo)} has no ratings yet")
        return Fact(out, (call,))
