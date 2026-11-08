"""Writing a walk's journal to storage: `walk --journal URL` (or PUBKIT_JOURNAL_URL).

The deploy-journal/2 document is uploaded as a file of the tenant `plane`,
then the head services/OWNER/REPO/UNIT moved to it, If-Match the head's
ETag, so two walks never lose one's journal: a move another walk won is
tried once more, on the head it left. The token is PUBKIT_STORAGE_TOKEN; in
GitHub Actions, without it, the job's OIDC token for the storage's audience
(PUBKIT_STORAGE_AUDIENCE, else the URL's host), as board-publish signs in.
A laptop has neither, and writes no journal. A journal that could not be
written is said, in one line, and never fails the walk.
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Mapping
from typing import Any

from ..board_publish import Refused, Storage, oidc_token

TENANT = "plane"


class Heads(Storage):
    """board_publish's Storage, for a head by path."""

    def head(self, path: str) -> str | None:
        status, body, headers = self._send("GET", f"/head/{path}")
        if status == 404:
            return None
        if status != 200:
            raise Refused(status, body)
        return headers.get("ETag", "").strip('"') or None

    def point(self, path: str, hash_: str, current: str | None) -> int:
        condition = {"If-Match": f'"{current}"'} if current else {"If-None-Match": "*"}
        status, body, _ = self._send("PUT", f"/head/{path}", json.dumps({"hash": hash_}).encode(),
                                     {"Content-Type": "application/json", **condition})
        if status not in (200, 412):
            raise Refused(status, body)
        return status


def token(url: str, env: Mapping[str, str]) -> str | None:
    if env.get("PUBKIT_STORAGE_TOKEN"):
        return env["PUBKIT_STORAGE_TOKEN"]
    if env.get("ACTIONS_ID_TOKEN_REQUEST_URL") and env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN"):
        return oidc_token(env.get("PUBKIT_STORAGE_AUDIENCE") or urllib.parse.urlsplit(url).hostname or "")
    return None


def write(url: str, document: dict[str, Any], env: Mapping[str, str], storage: Any = None) -> str:
    """Write the journal; one line saying what was done, or why not."""
    service = document["service"]
    path = f"services/{service['repository'] or 'unknown/unknown'}/{service['unit']}"
    try:
        if storage is None:
            given = token(url, env)
            if given is None:
                return "deploy: no journal written: no storage token here (PUBKIT_STORAGE_TOKEN, or a GitHub Actions job's)"
            storage = Heads(url, TENANT, given)
        new = storage.upload(json.dumps(document, indent=2).encode())
        for _ in range(2):
            if storage.point(path, new, storage.head(path)) == 200:
                return f"deploy: journal {new} written, the head {path} moved to it"
        return f"deploy: journal {new} written, but the head {path} moved under it twice: it was left"
    except Refused as e:
        return f"deploy: no journal written: storage said {e}"
    except (OSError, ValueError, KeyError) as e:
        return f"deploy: no journal written: {type(e).__name__}"
