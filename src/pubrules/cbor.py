"""Publet's canonical CBOR, enough to write back an object `pub read --json`
shows: integers, text, byte strings (`{"$bytes": hex}`), arrays and maps
with keys in byte order. Every object written this way is checked against
its identifier, so a mistake here cannot write a wrong object, only fail."""

from __future__ import annotations

import base64
import hashlib


def head(major: int, n: int) -> bytes:
    if n < 24:
        return bytes([major << 5 | n])
    for size, code in ((1, 24), (2, 25), (4, 26), (8, 27)):
        if n < 1 << (8 * size):
            return bytes([major << 5 | code]) + n.to_bytes(size, "big")
    raise ValueError(f"{n} does not fit in CBOR's lengths")


def encode(value) -> bytes:
    if isinstance(value, bool):
        return b"\xf5" if value else b"\xf4"
    if value is None:
        return b"\xf6"
    if isinstance(value, int):
        return head(0, value) if value >= 0 else head(1, -1 - value)
    if isinstance(value, str):
        b = value.encode()
        return head(3, len(b)) + b
    if isinstance(value, list):
        return head(4, len(value)) + b"".join(encode(x) for x in value)
    if isinstance(value, dict):
        if set(value) == {"$bytes"}:
            b = bytes.fromhex(value["$bytes"])
            return head(2, len(b)) + b
        items = sorted(value.items(), key=lambda kv: kv[0].encode())
        return head(5, len(items)) + b"".join(encode(k) + encode(v) for k, v in items)
    raise TypeError(f"no canonical CBOR for {type(value).__name__}")


def cid(data: bytes) -> str:
    """Section 4.2's identifier of `data`."""
    digest = hashlib.sha256(data).digest()
    return "pub:sha2-256:" + base64.b32encode(digest).decode().lower().rstrip("=")
