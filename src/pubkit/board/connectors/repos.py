"""The repos connector: a repos file, or until they go, protected.json and
governed.json, which say which repositories the board grades."""

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

    def _entries(self, method: str, path: Path) -> Fact:
        # Logged by name only: board.json does not name the runner's directories.
        response, call = self.get(method, f"file:{path}", f"file:{path.name}")
        if not response.ok:
            return Fact(None, (call,), response.message())
        try:
            entries = response.json()["repositories"]
        except (ValueError, KeyError, TypeError) as e:
            return Fact(None, (call,), f"{path.name} needs a \"repositories\" list ({e})")
        if not isinstance(entries, list) or not all(
                isinstance(e, dict) and isinstance(e.get("repo"), str) for e in entries):
            return Fact(None, (call,), f"{path.name}: every entry needs a repo, OWNER/NAME")
        return Fact(entries, (call,))

    def protected(self, path: Path) -> Fact:
        """protected.json's entries: each repository, its checks and tags."""
        return self._entries("protected", path)

    def governed(self, path: Path) -> Fact:
        """governed.json's entries: the repositories pubkit's nightly updates."""
        return self._entries("governed", path)
