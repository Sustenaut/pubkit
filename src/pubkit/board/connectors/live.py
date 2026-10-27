"""The live connector: the services themselves, over HTTPS, at the
health URL each declares in its pubkit.json."""

from __future__ import annotations

from .base import Connector, Fact


class Live(Connector):
    name = "live"

    def health(self, url: str) -> Fact:
        """Whether the URL answers with a success, and its status."""
        response, call = self.get("health", url)
        if response.error:
            return Fact({"answers": False, "status": None}, (call,), response.error)
        return Fact({"answers": response.ok, "status": response.status}, (call,))
