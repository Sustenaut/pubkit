"""The scheduled walk: pubkit's reusable deploy-check.yml, and the caller
`pubkit sync` seeds in a repository with a deploy block, once, as the
repository's own from then on. pubkit bump moves its pin with the others.
"""

from __future__ import annotations

from typing import Any

CALLER = ".github/workflows/deploy-check.yml"


def caller(config: dict[str, Any]) -> str:
    from .. import __version__  # noqa: PLC0415
    from ..refs import owner, ref, release_commit  # noqa: PLC0415
    pin = ref(__version__, release_commit())
    return (f"# Seeded by pubkit {__version__}: this repository's own. Every 30 minutes, the deploy walk, read-only.\n"
            "name: deploy-check\n"
            "on: { schedule: [{ cron: '*/30 * * * *' }], workflow_dispatch: {} }\n"
            "permissions: { actions: read, contents: read, id-token: write }\n"
            "jobs:\n"
            "  check:\n"
            f"    uses: {owner(config)}/pubkit/.github/workflows/deploy-check.yml@{pin}\n"
            "    secrets: inherit\n")
