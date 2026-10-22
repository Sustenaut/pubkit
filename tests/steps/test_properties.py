"""Hypothesis properties of the code that turns data into bytes or lists:
publet's canonical CBOR, and the nightly's list of moved packages.

`decode` is the reference the encoder is checked against: RFC 8949's major
types 0 to 5 and the simple values pub read --json shows, nothing more.
"""

from __future__ import annotations

from hypothesis import given, strategies as st
from pytest_bdd import scenarios, then

from pubkit import cbor, nightly

scenarios("properties.feature")

# What `pub read --json` shows: integers in CBOR's 64-bit range, text,
# byte strings as {"$bytes": hex}, arrays and maps, true, false and null.
INTEGERS = st.integers(min_value=-(1 << 64), max_value=(1 << 64) - 1)
BYTES = st.binary(max_size=64).map(lambda b: {"$bytes": b.hex()})
SCALARS = st.one_of(st.none(), st.booleans(), INTEGERS, st.text(max_size=32), BYTES)
VALUES = st.recursive(
    SCALARS,
    lambda inner: st.one_of(
        st.lists(inner, max_size=6),
        st.dictionaries(st.text(max_size=12).filter(lambda k: k != "$bytes"), inner, max_size=6)),
    max_leaves=24)


def decode(data: bytes, at: int = 0) -> tuple[object, int]:
    """The value at `at`, and where the next one starts."""
    major, info = data[at] >> 5, data[at] & 0x1F
    at += 1
    if major == 7:
        return {20: False, 21: True, 22: None}[info], at
    if info < 24:
        n = info
    else:
        size = 1 << (info - 24)
        n, at = int.from_bytes(data[at:at + size], "big"), at + size
    if major == 0:
        return n, at
    if major == 1:
        return -1 - n, at
    if major in (2, 3):
        raw, at = data[at:at + n], at + n
        return ({"$bytes": raw.hex()} if major == 2 else raw.decode()), at
    items = []
    for _ in range(n if major == 4 else 2 * n):
        item, at = decode(data, at)
        items.append(item)
    return (items if major == 4 else dict(zip(items[::2], items[1::2], strict=True))), at


@then("every value pub read --json shows encodes to CBOR that decodes back to it")
def round_trips():
    @given(VALUES)
    def check(value):
        data = cbor.encode(value)
        assert decode(data) == (value, len(data))

    check()


@then("every map encodes to the same CBOR with its keys in any order, sorted by their bytes")
def key_order():
    @given(st.dictionaries(st.text(max_size=12).filter(lambda k: k != "$bytes"), INTEGERS,
                           min_size=1, max_size=8), st.randoms())
    def check(value, rnd):
        keys = list(value)
        rnd.shuffle(keys)
        shuffled = {k: value[k] for k in keys}
        data = cbor.encode(shuffled)
        assert data == cbor.encode(value)
        decoded, _ = decode(data)
        assert list(decoded) == sorted(value, key=str.encode)

    check()


@then("for any two locks, the packages listed as changed are those in both whose version differs")
def changed_packages():
    names = st.text("abcdefgh-", min_size=1, max_size=6)
    versions = st.from_regex(r"\A[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{1,2}\Z")

    @given(st.dictionaries(names, versions), st.dictionaries(names, versions))
    def check(old, new):
        listed = nightly.changed(old, new)
        moved = sorted(n for n in old if n in new and old[n] != new[n])
        assert listed == [f"{n} {old[n]} -> {new[n]}" for n in moved]

    check()
