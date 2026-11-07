"""Where a path given on the command line may point.

pubkit writes its reports and reads its inputs only inside the working
directory or a temporary directory (the system's, or the Actions runner's
`RUNNER_TEMP`), so a mistyped or malicious argument cannot reach a file
elsewhere.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def bases() -> list[Path]:
    """The directories a command-line path may lie in, resolved."""
    out = [Path.cwd(), Path(tempfile.gettempdir())]
    if os.environ.get("RUNNER_TEMP"):
        out.append(Path(os.environ["RUNNER_TEMP"]))
    return [base.resolve() for base in out]


def confined(path: Path | str, what: str, also: tuple[Path, ...] = ()) -> Path:
    """`path`, resolved, when it lies in the working directory, a temporary
    directory, or one of `also`; otherwise pubkit stops, naming `what`."""
    resolved = Path(path).resolve()
    if any(resolved.is_relative_to(base) for base in [*bases(), *(a.resolve() for a in also)]):
        return resolved
    where = " or ".join(["the working directory", "a temporary directory", *(str(a) for a in also)])
    sys.exit(f"pubkit: {what} ({path}) must be in {where}")
