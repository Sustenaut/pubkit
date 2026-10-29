"""Steps for `pubkit site check`, against small built sites written into
the scratch repository: one complete page with its card, and the changes
each scenario makes to it."""

from __future__ import annotations

import json
import struct

from pytest_bdd import given, parsers, scenarios, then

from pubkit import conform

scenarios("site.feature")

COMPLETE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Example</title>
<meta name="description" content="An example site.">
<meta property="og:type" content="website">
<meta property="og:title" content="Example">
<meta property="og:description" content="An example site.">
<meta property="og:url" content="https://example.eu/">
<meta property="og:image" content="https://example.eu/card.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
</head>
<body><h1>Example</h1></body>
</html>
"""


def png(width: int, height: int) -> bytes:
    """A PNG's signature and header: all the check reads."""
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)


def jpeg(width: int, height: int) -> bytes:
    """A JPEG's start, an APP0 segment and a baseline frame header."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


@given(parsers.parse('a built site in "{folder}" with a complete page "{page}"'))
def complete_site(repo, folder, page):
    (repo / folder).mkdir()
    (repo / folder / page).write_text(COMPLETE)
    (repo / folder / "card.png").write_bytes(png(1200, 630))


@given(parsers.parse('the site file "{path}" holds "{text}"'))
def site_file(repo, path, text):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text.replace("\\n", "\n").replace('\\"', '"') + "\n")


@given(parsers.parse('the page "{path}" has only a title'))
def bare_page(repo, path):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("<!doctype html><html><head><title>About</title></head><body></body></html>\n")


@given(parsers.parse('the page "{path}" has a language, a title and robots "{robots}"'))
def unlisted_page(repo, path, robots):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('<!doctype html><html lang="en"><head><title>Storage</title>'
                      f'<meta name="robots" content="{robots}"></head><body></body></html>\n')


@given(parsers.parse('the page "{path}" has only a title and robots "{robots}"'))
def bare_unlisted_page(repo, path, robots):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('<!doctype html><html><head><title>Storage</title>'
                      f'<meta name="robots" content="{robots}"></head><body></body></html>\n')


@given("the site's card is a 640×480 JPEG")
def small_card(repo):
    # Named .png still: the check reads the bytes, not the name.
    (repo / "_site" / "card.png").write_bytes(jpeg(640, 480))


@given("the site's card is removed")
def card_removed(repo):
    (repo / "_site" / "card.png").unlink()


@given("the site's card is a WebP")
def webp_card(repo):
    (repo / "_site" / "card.png").write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 ")


@given(parsers.parse('pubkit.json declares exceptions for "{rules}" until "{until}"'))
def site_exceptions(repo, env, rules, until):
    declared = [{"rule": r.strip(), "reason": "the card is coming", "until": until}
                for r in rules.split(",")]
    (repo / "pubkit.json").write_text(json.dumps({"kind": "service", "exceptions": declared}))
    env["PUBKIT_TODAY"] = "2026-10-28"


@then(parsers.parse('stderr does not mention "{text}"'))
def stderr_lacks(result, text):
    assert text not in result.stderr, result.stderr


@then("pubkit conform reads no exception from it")
def conform_skips_site_rules(repo):
    config = json.loads((repo / "pubkit.json").read_text())
    known = {"RS-DOC-01"}
    assert conform.exceptions(config, known) == (set(), [], [])
