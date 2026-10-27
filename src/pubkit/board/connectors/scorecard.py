"""The scorecard connector: the OpenSSF Scorecard API, for a public
repository's score out of 10 and the checks scoring under it."""

from __future__ import annotations

from .base import Connector, Fact

API = "https://api.securityscorecards.dev"


class Scorecard(Connector):
    name = "scorecard"

    def score(self, repo: str) -> Fact:
        """The score and the checks below 10; None when Scorecard has not scored it."""
        response, call = self.get("score", f"{API}/projects/github.com/{repo}")
        if response.status == 404 or (response.ok and not response.body.strip()):
            return Fact(None, (call,), "not scored by OpenSSF Scorecard")
        if not response.ok:
            return Fact(None, (call,), response.message())
        data = response.json() or {}
        failing = sorted(c["name"] for c in data.get("checks", [])
                         if isinstance(c.get("score"), int | float) and 0 <= c["score"] < 10)
        return Fact({"score": data.get("score"), "failing": failing}, (call,))
