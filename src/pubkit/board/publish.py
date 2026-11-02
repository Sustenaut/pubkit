"""`pubkit board-publish`: keep a new board in storage, if it says anything new, and point
its head at it.

    pubkit board-publish --url https://storage.example.org --tenant some-tenant [--file site/board.json]

A tenant's `board` head names its latest snapshot. A board differs from the last only if
a fact or a grade does: when it was read and the calls that read it don't count. So an
unchanged run uploads nothing, and every snapshot kept is a change.

It signs in with the GitHub Actions job's OpenID Connect token, for the audience that is
the storage's host name, so the job holds no secret. It prints no repository's name, since
the logs of a public repository's job are public, and a state may name private ones.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

# Cloudflare refuses Python's default User-Agent (error 1010).
AGENT = "pubkit-board-publish"
TENANT = re.compile(r"[a-z0-9][a-z0-9-]*")
# What changes every time a board is read, and says nothing about the repositories.
READING = {"built", "calls", "summary", "sources"}


def facts_and_grades(value: Any) -> Any:
    """A board without what only says when and how it was read."""
    if isinstance(value, dict):
        return {k: facts_and_grades(v) for k, v in value.items() if k not in READING}
    if isinstance(value, list):
        return [facts_and_grades(v) for v in value]
    return value


def changed(new: bytes, last: bytes | None) -> bool:
    if last is None:
        return True
    return facts_and_grades(json.loads(new)) != facts_and_grades(json.loads(last))


class Refused(Exception):
    """Storage's answer to a request it didn't carry out."""

    def __init__(self, status: int, body: bytes):
        self.status = status
        super().__init__(f"storage said {status}: {body.decode('utf-8', 'replace')[:300]}")


def oidc_token(audience: str) -> str:
    url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
    separator = "&" if "?" in url else "?"
    request = urllib.request.Request(f"{url}{separator}audience={urllib.parse.quote(audience)}", headers={
        "Authorization": f"Bearer {os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']}"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - GitHub's own address
        return json.load(response)["value"]


class Storage:
    def __init__(self, base: str, tenant: str, token: str):
        self.base, self.tenant, self.token = base.rstrip("/"), tenant, token

    def _send(self, method: str, path: str, data: bytes | None = None,
              headers: dict | None = None) -> tuple[int, bytes, dict]:
        request = urllib.request.Request(
            f"{self.base}/{self.tenant}{path}", data=data, method=method,
            headers={"Authorization": f"Bearer {self.token}", "User-Agent": AGENT, **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - the caller's --url
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def latest(self) -> tuple[str | None, bytes | None]:
        """The hash the head points at and its file, or None for both before its first move."""
        status, body, headers = self._send("GET", "/head/board")
        if status == 404:
            return None, None
        if status != 200:
            raise Refused(status, body)
        return headers["ETag"].strip('"'), body

    def upload(self, board: bytes) -> str:
        boundary = uuid.uuid4().hex
        form = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="board.json"\r\n'
                f"Content-Type: application/json\r\n\r\n").encode() + board + f"\r\n--{boundary}--\r\n".encode()
        status, body, _ = self._send("POST", "/files", form,
                                     {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        if status != 200:
            raise Refused(status, body)
        return json.loads(body)["id"]

    def move(self, hash_: str, current: str | None) -> None:
        condition = {"If-Match": f'"{current}"'} if current else {"If-None-Match": "*"}
        status, body, _ = self._send("PUT", "/head/board", json.dumps({"hash": hash_}).encode(),
                                     {"Content-Type": "application/json", **condition})
        if status != 200:
            raise Refused(status, body)


def run(url: str, tenant: str, file: Path) -> int:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        sys.exit(f"pubkit board-publish: --url must be an address, not {url}")
    if not TENANT.fullmatch(tenant):
        sys.exit(f"pubkit board-publish: --tenant must be lower case letters, digits and -, not {tenant}")
    try:
        board = file.read_bytes()
    except OSError as e:
        sys.exit(f"pubkit board-publish: {file}: {e.strerror}")
    for name in ("ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"):
        if not os.environ.get(name):
            sys.exit(f"pubkit board-publish: no {name}: the job needs the permission id-token: write")
    storage = Storage(url, tenant, oidc_token(parsed.hostname))
    try:
        current, last = storage.latest()
        if not changed(board, last):
            print(f"{tenant}: nothing changed since {current}; nothing uploaded")
            return 0
        new = storage.upload(board)
        storage.move(new, current)
    except Refused as e:
        print(f"{tenant}: {e}", file=sys.stderr)
        return 1
    print(f"{tenant}: the board head now points at {new}")
    return 0
