"""Steps for refs.SHIPPED against the repository's tree."""

from __future__ import annotations

from pathlib import Path

from pytest_bdd import scenarios, then, when

from pubkit.refs import SHIPPED

scenarios("shipped.feature")

ROOT = Path(__file__).resolve().parents[2]


@when("I compare refs.SHIPPED with the repository's tree", target_fixture="tree")
def tree():
    workflows = ROOT / ".github" / "workflows"
    return {
        "actions": sorted(p.name for p in (ROOT / "actions").iterdir() if (p / "action.yml").is_file()),
        "workflows": sorted(p.name for p in workflows.glob("*.yml")
                            if "workflow_call:" in p.read_text(encoding="utf-8")),
        "terraform": sorted(p.name for p in (ROOT / "terraform").glob("*")
                            if p.is_dir() and (p / "main.tf").is_file()),
    }


@then("they name the same actions, reusable workflows and modules")
def same(tree):
    assert {k: sorted(v) for k, v in SHIPPED.items()} == tree
