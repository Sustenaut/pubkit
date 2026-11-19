"""Steps checking the plugin marketplace's layout."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pytest_bdd import scenarios, then, when

scenarios("../features/plugin-marketplace.feature")

SOURCE = Path(__file__).resolve().parents[2]


def read_market() -> dict:
    """marketplace.json, and the directory each plugin it lists is installed from."""
    market = json.loads((SOURCE / ".claude-plugin" / "marketplace.json").read_text())
    return {"market": market, "roots": [(SOURCE / p["source"]).resolve() for p in market["plugins"]]}


@when("I read the plugin marketplace pubrules is", target_fixture="marketplace")
def read(pubtest_ctx):
    return pubtest_ctx.attempt(read_market)


def plugins(marketplace) -> list[Path]:
    market = marketplace["market"]
    assert market["name"] and market["owner"]["name"], market
    return marketplace["roots"]


def frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert match, "no frontmatter"
    return dict(line.split(": ", 1) for line in match.group(1).splitlines() if ": " in line)


@then("every plugin the marketplace lists has a plugin.json naming it")
def manifests(marketplace):
    for entry, root in zip(marketplace["market"]["plugins"], plugins(marketplace)):
        manifest = json.loads((root / ".claude-plugin" / "plugin.json").read_text())
        assert manifest["name"] == entry["name"] == root.name, (entry, manifest)


@then("every plugin agent states a name and a description")
def agents(marketplace):
    found = [a for root in plugins(marketplace) for a in (root / "agents").glob("*.md")]
    assert found
    for agent in found:
        meta = frontmatter(agent.read_text())
        assert meta.get("name") == agent.stem, agent
        assert len(meta.get("description", "")) > 40, agent


@then("every ${CLAUDE_PLUGIN_ROOT} path an agent names exists in its plugin")
def shipped(marketplace):
    for root in plugins(marketplace):
        for agent in (root / "agents").glob("*.md"):
            for rel in re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/([\w./-]+[\w])", agent.read_text()):
                assert (root / rel).is_file(), f"{agent.name} names {rel}, which is not shipped"
