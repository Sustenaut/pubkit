"""`pubkit site check DIR`: what a public site's built pages need so that
robots and other sites read them correctly.

Each check belongs to a rule with a stable ID, so a repository can declare
an exception for it in `pubkit.json` as for the Rust crate standard
(`pubkit conform`). The rules grow by family: WEB-PAGE for what every page
names about itself, WEB-OG for the card a shared link shows (LinkedIn,
Facebook, Slack, WhatsApp...), WEB-ROBOTS for what robots.txt lets in. A
later family (accessibility, say) is a new entry in RULES and a function in
CHECKS.

It reads the built directory only, never the network: an og:image under the
site's own address (--url) is looked up in DIR; one elsewhere is only
checked to be an https address. Pages that are not for sharing are left
out: 404.html, and redirects (`<meta http-equiv="refresh">`). A page that
asks not to be indexed (`<meta name="robots" content="noindex">`, or
"none") is not for sharing or search either, so only the rules that hold
for any page apply to it: its language and title, not its description,
card or image, nor what robots.txt lets in.

It uses only the standard library, so deploy-static runs it from pubkit's
source without installing pubkit.
"""

from __future__ import annotations

import json
import struct
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

RULES = {
    "WEB-PAGE-01": "every page names its language: <html lang>",
    "WEB-PAGE-02": "every page has a <title> and a <meta name=\"description\">",
    "WEB-OG-01": "every page has an Open Graph card: og:title, og:description, og:type, og:url, og:image",
    "WEB-OG-02": "og:image is an https address of a PNG or JPEG of at least 1200×627 and under 5 MB",
    "WEB-OG-03": "every page asks for a large card: <meta name=\"twitter:card\" content=\"summary_large_image\">",
    "WEB-ROBOTS-01": "robots.txt lets the link-preview bots read every page and its og:image",
}

# The crawlers that draw link previews. Each obeys robots.txt, so a
# Disallow meant for search engines also hides the card.
PREVIEW_BOTS = ("LinkedInBot", "facebookexternalhit", "Twitterbot", "Slackbot")

# LinkedIn's minimum for a large card, and its largest image.
MIN_WIDTH, MIN_HEIGHT = 1200, 627
MAX_BYTES = 5 * 1024 * 1024

# The address pages are checked against robots.txt at without --url: only
# the path matters to it.
PLACEHOLDER = "https://site.invalid/"


class Page(HTMLParser):
    """The parts of one HTML page the rules read."""

    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.lang = ""
        self.title = ""
        self.meta: dict[str, str] = {}
        self.redirect = False
        self.noindex = False
        self._in_title = False
        self.feed(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang", "").strip()
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            if a.get("http-equiv", "").lower() == "refresh":
                self.redirect = True
            if a.get("name", "").lower() == "robots":
                words = a.get("content", "").lower().replace(",", " ").split()
                self.noindex = self.noindex or "noindex" in words or "none" in words
            key = a.get("property") or a.get("name")
            if key:
                self.meta.setdefault(key.lower(), a.get("content", "").strip())

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


def image_size(data: bytes) -> tuple[str, int, int] | None:
    """(format, width, height) of a PNG or JPEG, read from its header, or
    None for anything else."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
        return "PNG", width, height
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        (length,) = struct.unpack(">H", data[i + 2:i + 4])
        # Start of frame: every SOFn but DHT (C4), JPG (C8) and DAC (CC).
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return "JPEG", width, height
        i += 2 + length
    return None


def pages(root: Path) -> list[Path]:
    """Every HTML page under `root` that is meant to be shared."""
    return sorted(p for p in root.rglob("*.html") if p.name != "404.html")


def address(root: Path, path: Path, base: str) -> str:
    """The address a page in `root` is served at under `base`."""
    rel = path.relative_to(root).as_posix()
    if rel == "index.html" or rel.endswith("/index.html"):
        rel = rel[: -len("index.html")]
    return urljoin(base, rel)


def local(root: Path, url: str, base: str | None) -> Path | None:
    """The file in `root` an address under `base` is, or None when the
    address is elsewhere."""
    if not base or not url.startswith(base):
        return None
    rel = urlsplit(url).path[len(urlsplit(base).path):]
    target = (root / rel).resolve()
    if not target.is_relative_to(root.resolve()):
        return None
    return target / "index.html" if target.is_dir() else target


def check_page(rel: str, page: Page) -> dict[str, list[str]]:
    """The WEB-PAGE and WEB-OG-01/03 problems of one page; for a page that
    asks not to be indexed, only those of WEB-PAGE-01 and its title."""
    out: dict[str, list[str]] = {}

    def problem(rule: str, text: str) -> None:
        out.setdefault(rule, []).append(f"{rel}: {text}")

    if not page.lang:
        problem("WEB-PAGE-01", 'no <html lang>; set it to the page\'s language, like <html lang="en">, '
                               "so screen readers and translators read it right")
    if not page.title.strip():
        problem("WEB-PAGE-02", "no <title>; give the page a title, which search results and tabs show")
    if page.noindex:
        return out
    if not page.meta.get("description"):
        problem("WEB-PAGE-02", 'no <meta name="description">; add one or two sentences on what the page is')
    missing = [k for k in ("og:title", "og:description", "og:type", "og:url", "og:image")
               if not page.meta.get(k)]
    if missing:
        problem("WEB-OG-01", f"no {', '.join(missing)}; add "
                + " ".join(f'<meta property="{k}" content="...">' for k in missing)
                + " to its <head>, or a shared link shows no card")
    if page.meta.get("og:url") and not page.meta["og:url"].startswith("https://"):
        problem("WEB-OG-01", f"og:url is {page.meta['og:url']}; give the page's full https:// address")
    if page.meta.get("twitter:card") != "summary_large_image":
        problem("WEB-OG-03", '<meta name="twitter:card" content="summary_large_image"> is missing; '
                             "without it some sites show the image as a small thumbnail")
    return out


def check_image(root: Path, rel: str, page: Page, base: str | None,
                seen: dict[Path, str | None]) -> list[str]:
    """The WEB-OG-02 problems of one page's og:image."""
    url = page.meta.get("og:image")
    if not url:
        return []
    if not url.startswith("https://"):
        return [f"{rel}: og:image is {url}; give its full https:// address, which a crawler can fetch"]
    path = local(root, url, base)
    if path is None:
        return []
    if path not in seen:
        seen[path] = image_problem(path, url)
    problems = [f"{rel}: {seen[path]}"] if seen[path] else []
    size = image_size(path.read_bytes()) if path.is_file() else None
    for key, index in (("og:image:width", 1), ("og:image:height", 2)):
        declared = page.meta.get(key)
        if size and declared and declared != str(size[index]):
            problems.append(f"{rel}: {key} says {declared}, but {url} is {size[index]}; "
                            "make them agree, or a card is laid out wrong")
    return problems


def image_problem(path: Path, url: str) -> str | None:
    if not path.is_file():
        return f"og:image {url} is not in the site; build it, or point og:image at a file that is"
    data = path.read_bytes()
    size = image_size(data)
    if size is None:
        return f"og:image {url} is not a PNG or JPEG, which LinkedIn needs; save it as one"
    _, width, height = size
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        return (f"og:image {url} is {width}×{height}; make it at least {MIN_WIDTH}×{MIN_HEIGHT} "
                "(1200×630 is usual), or LinkedIn shows no image")
    if len(data) > MAX_BYTES:
        return f"og:image {url} is {len(data) // 1024} KB; keep it under 5 MB, or LinkedIn drops it"
    return None


def check_robots(root: Path, urls: list[str]) -> list[str]:
    """The WEB-ROBOTS-01 problems: an address in `urls` that robots.txt
    keeps a preview bot from."""
    robots_txt = root / "robots.txt"
    if not robots_txt.is_file():
        return []
    robots = RobotFileParser()
    robots.parse(robots_txt.read_text(encoding="utf-8", errors="replace").splitlines())
    problems = []
    for bot in PREVIEW_BOTS:
        blocked = [u for u in urls if not robots.can_fetch(bot, u)]
        if blocked:
            shown = ", ".join(blocked[:3]) + (f" and {len(blocked) - 3} more" if len(blocked) > 3 else "")
            problems.append(f"robots.txt keeps {bot} from {shown}, so links to it show no card; "
                            f"add a group for the preview bots ({', '.join(PREVIEW_BOTS)}) with Allow: /, "
                            "and keep search engines out with <meta name=\"robots\" content=\"noindex\"> instead")
    return problems


def checks(root: Path, base: str | None) -> tuple[int, dict[str, list[str]]]:
    """(pages checked, problems by rule)."""
    out: dict[str, list[str]] = {}
    seen: dict[Path, str | None] = {}
    count, urls = 0, []
    for path in pages(root):
        page = Page(path.read_text(encoding="utf-8", errors="replace"))
        if page.redirect:
            continue
        count += 1
        rel = path.relative_to(root).as_posix()
        for rule, texts in check_page(rel, page).items():
            out.setdefault(rule, []).extend(texts)
        if page.noindex:
            continue
        if image := check_image(root, rel, page, base, seen):
            out.setdefault("WEB-OG-02", []).extend(image)
        urls.append(address(root, path, base or PLACEHOLDER))
        if base and page.meta.get("og:image", "").startswith(base):
            urls.append(page.meta["og:image"])
    if robots := check_robots(root, list(dict.fromkeys(urls))):
        out["WEB-ROBOTS-01"] = robots
    return count, out


def run(root: Path, directory: Path, base: str | None) -> int:
    from .conform import exceptions

    if base is not None:
        if not base.startswith("https://"):
            sys.exit(f"pubkit: --url ({base}) must be the site's https:// address")
        base = base if base.endswith("/") else base + "/"
    if not directory.is_dir():
        sys.exit(f"pubkit: {directory} is not a directory; build the site first")
    config_file = root / "pubkit.json"
    config = json.loads(config_file.read_text()) if config_file.is_file() else {}
    waived, open_, problems = exceptions(config, set(RULES), family=lambda r: str(r).startswith("WEB-"))
    count, found = checks(directory, base)
    if count == 0:
        problems.append(f"{directory} has no HTML pages to check; is it the built site?")
    for rule, texts in sorted(found.items()):
        if rule not in waived:
            problems += [f"{rule} {t}" for t in texts]
    for e in open_:
        print(f"open exception: {e}")
    if problems:
        for p in problems:
            print(p, file=sys.stderr)
        print(f"pubkit site check: {len(problems)} problem(s) in {count} page(s)", file=sys.stderr)
        return 1
    print(f"{count} page(s) meet the {len(RULES) - len(waived)} site rules"
          + ("" if base else "; give --url to check the og:image files too"))
    return 0


def main(argv: list[str] | None = None) -> int:
    """`python -m pubkit.site check DIR [--url URL]`, as deploy-static runs
    it without installing pubkit."""
    import argparse

    parser = argparse.ArgumentParser(prog="pubkit site")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("check")
    p.add_argument("dir", type=Path)
    p.add_argument("--url")
    args = parser.parse_args(argv)
    return run(Path.cwd(), args.dir, args.url)


if __name__ == "__main__":
    sys.exit(main())
