"""What every repository's .gitignore must ignore, and the healing that adds it.

Local tools read secrets from `.env`: with-secrets reads its Bitwarden token
there. A repository that doesn't ignore it is one `git add -A` from committing
that token. The rest of .gitignore is the repository's own, so `sync` only
appends what is missing, and `sync --check` fails while anything is.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path

GITIGNORE = ".gitignore"
# Each name, and the comment written above it when sync adds it.
REQUIRED = {".env": "Local secrets, such as the Bitwarden token ./with-secrets reads: never committed."}


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


def heal(root: Path) -> list[str]:
    """Append each missing name to .gitignore, leaving the rest as it is. Returns
    what it added."""
    added = missing(root)
    if added:
        path = root / GITIGNORE
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if text and not text.endswith("\n"):
            text += "\n"
        if text:
            text += "\n"
        text += "".join(f"# {REQUIRED[name]}\n{name}\n" for name in added)
        path.write_text(text, encoding="utf-8")
    return added
