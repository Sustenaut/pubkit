"""The repos connector: a repos file, which says which repositories the board grades."""

from __future__ import annotations

from pathlib import Path

from ...repos import entries
from .base import Connector, Fact


class Repos(Connector):
    name = "repos"

    def listed(self, path: Path) -> Fact:
        """A repos file's entries: each repository and its roles."""
        response, call = self.get("listed", f"file:{path}", f"file:{path.name}")
        if not response.ok:
            return Fact(None, (call,), response.message())
        try:
            return Fact(entries(response.body or b"", path.name), (call,))
        except ValueError as e:
            return Fact(None, (call,), str(e))
