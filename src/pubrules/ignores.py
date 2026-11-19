"""What every repository's .gitignore must ignore, and the healing that adds it.

Local tools read secrets from `.env`: with-secrets reads its Bitwarden token
there. A repository that doesn't ignore it is one `git add -A` from committing
that token. `.venv` is the local environment, and pubrel's prepare links the
checkout's into its worktree: `.venv/` matches only a directory, so a link
went into a release commit. The rest of .gitignore is the repository's own,
so `sync` only mends what is missing, and `sync --check` fails while anything
is: a pattern that matches the name only as a directory (`/.venv/`) loses its
slash where it stands, and a name no line covers is appended.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path

GITIGNORE = ".gitignore"
# Each name, and the comment written above it when sync adds it.
REQUIRED = {
    ".env": "Local secrets, such as the Bitwarden token ./with-secrets reads: never committed.",
    ".venv": "The local environment, a directory or a link (pubrel prepare links it into its worktree).",
}


def ignores(text: str, name: str) -> bool:
    """Whether `text`, a .gitignore, ignores the file `name` at the repository's
    root: the last pattern that matches it decides, and `!` takes it back."""
    out = False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        pattern = line[1:] if negated else line
        if pattern.endswith("/"):  # only directories
            continue
        pattern = pattern.removeprefix("**/").removeprefix("/")
        if fnmatchcase(name, pattern):
            out = not negated
    return out


def missing(root: Path) -> list[str]:
    path = root / GITIGNORE
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    return [name for name in REQUIRED if not ignores(text, name)]


def _undirected(line: str, name: str) -> str | None:
    """`line` without its trailing slash, when it ignores `name` only as a
    directory (`/.venv/` for `.venv`); None for any other line."""
    stripped = line.strip()
    if stripped.startswith(("!", "#")) or not stripped.endswith("/"):
        return None
    pattern = stripped.rstrip("/")
    return pattern if pattern and ignores(pattern, name) else None


def heal(root: Path) -> list[str]:
    """Mend .gitignore for each missing name, leaving the rest as it is: a
    line that ignores it only as a directory loses its slash where it stands,
    and a name still not ignored is appended under its comment. Returns what
    it mended."""
    added = missing(root)
    if added:
        path = root / GITIGNORE
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        lines = text.splitlines()
        for name in added:
            for n, line in enumerate(lines):
                if (pattern := _undirected(line, name)) is not None:
                    lines[n] = pattern
        text = "".join(f"{line}\n" for line in lines)
        appended = [name for name in added if not ignores(text, name)]
        if appended and text:
            text += "\n"
        text += "".join(f"# {REQUIRED[name]}\n{name}\n" for name in appended)
        path.write_text(text, encoding="utf-8")
    return added
