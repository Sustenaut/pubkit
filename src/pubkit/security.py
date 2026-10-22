"""The `security` block of pubkit.json, beyond fuzzing (see nightly.py).

`security.dependabot` is the Dependabot configuration `pubkit sync` writes
to `.github/dependabot.yml`:

    "dependabot": {"schedule": "weekly", "ecosystems": ["github-actions"],
                   "options": {"open-pull-requests-limit": 5}}

`schedule` is Dependabot's interval, `ecosystems` the package ecosystems it
updates, each at the repository's root unless given as Dependabot's own
`{"package-ecosystem": ..., "directory": ...}`, and `options` any other
Dependabot setting, added to every ecosystem's entry. The default is weekly
updates of the GitHub Actions alone: the nightly pull request updates the
languages, gated on CI, so Dependabot doing the same would open duplicate
pull requests. `"dependabot": false` leaves `.github/dependabot.yml` to the
repository.

`security.sonar` is `{"args": "-Dsonar.exclusions=vendor/**"}`: properties
every workflow's sonar job adds, after its `sonar-args` input.

The advisory checks (cargo deny, npm audit, pip-audit, OSV-Scanner,
dependency review) have no setting: every run makes them.
"""

from __future__ import annotations

import json
import sys
from typing import Any

DEPENDABOT_PATH = ".github/dependabot.yml"
SCHEDULES = ("daily", "weekly", "monthly")
DEPENDABOT = {"schedule": "weekly", "ecosystems": ["github-actions"], "options": {}}
SONAR = ("args",)


def block(config: dict[str, Any], name: str) -> Any:
    security = config.get("security", {})
    return security.get(name) if isinstance(security, dict) else None


def dependabot_settings(config: dict[str, Any]) -> dict[str, Any] | None:
    """`security.dependabot` over the defaults, or None when it is false."""
    given = block(config, "dependabot")
    if given is False:
        return None
    given = {} if given is None else given
    if not isinstance(given, dict):
        sys.exit("pubkit: pubkit.json's security.dependabot must be an object, such as "
                 '{"schedule": "weekly"}, or false')
    unknown = sorted(set(given) - set(DEPENDABOT))
    if unknown:
        sys.exit(f"pubkit: pubkit.json's security.dependabot has no setting {', '.join(unknown)}; "
                 f"it takes {', '.join(DEPENDABOT)}, and other Dependabot settings go in options")
    out = {**DEPENDABOT, **given}
    if out["schedule"] not in SCHEDULES:
        sys.exit(f"pubkit: security.dependabot.schedule must be one of {', '.join(SCHEDULES)}, "
                 f"not {out['schedule']!r}")
    ecosystems = out["ecosystems"]
    if not isinstance(ecosystems, list) or not ecosystems or not all(
            isinstance(e, str) or (isinstance(e, dict) and isinstance(e.get("package-ecosystem"), str))
            for e in ecosystems):
        sys.exit("pubkit: security.dependabot.ecosystems must list ecosystems, each a name such "
                 'as "npm" or {"package-ecosystem": "pip", "directory": "/tests"}')
    if not isinstance(out["options"], dict):
        sys.exit("pubkit: security.dependabot.options must be an object of Dependabot settings")
    return out


def dependabot(config: dict[str, Any]) -> str | None:
    """The text of .github/dependabot.yml, or None when pubkit leaves it to
    the repository. Each entry is JSON, which YAML reads as it is."""
    settings = dependabot_settings(config)
    if settings is None:
        return None
    lines = ["# Written by `pubkit sync` from security.dependabot in pubkit.json. Do not edit:",
             "# change pubkit.json and run `pubkit sync`.", "version: 2", "updates:"]
    for ecosystem in settings["ecosystems"]:
        entry = ({"package-ecosystem": ecosystem, "directory": "/"}
                 if isinstance(ecosystem, str) else {"directory": "/", **ecosystem})
        entry = {**entry, "schedule": {"interval": settings["schedule"]}, **settings["options"]}
        lines.append(f"  - {json.dumps(entry)}")
    return "\n".join(lines) + "\n"


def sonar_settings(config: dict[str, Any]) -> dict[str, str]:
    given = block(config, "sonar")
    given = {} if given is None else given
    if not isinstance(given, dict):
        sys.exit('pubkit: pubkit.json\'s security.sonar must be an object, such as {"args": "..."}')
    unknown = sorted(set(given) - set(SONAR))
    if unknown:
        sys.exit(f"pubkit: pubkit.json's security.sonar has no setting {', '.join(unknown)}; "
                 f"it takes {', '.join(SONAR)}")
    if not isinstance(given.get("args", ""), str):
        sys.exit("pubkit: security.sonar.args must be a string of -D properties")
    return {"args": given.get("args", "")}
