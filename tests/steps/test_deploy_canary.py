"""The canary: no secret value ever reaches what `pubkit deploy` prints.

Every fake Secret value and every fake Bitwarden value holds CANARY. Each
test drives the checks through a state where values are read and judged,
and what comes out (stdout, stderr, the journal, any exception's text) must
not hold it. This test must never be weakened: a check that needs to show a
value is wrong, not the test."""

from __future__ import annotations

import pytest

from deploy_world import CANARY, run, world  # noqa: F401 - the fixture
from pubkit.deploy import load
from pubkit.deploy.model import Need, Result
from pubkit.deploy.registry import types
from pubkit.deploy.walk import walk

STATES = {
    "all well": lambda w: None,
    "a bad cookie in the cluster": lambda w: w.cluster["namespaces"]["example"]["secrets"]["example-page-secrets"]
    .update({"cookie-secret": (CANARY + "+/+/").encode()}),
    "a bad cookie in Bitwarden": lambda w: w.env.update({"EXAMPLE_COOKIE_SECRET": CANARY + "+/+/" * 6}),
    "a variable that differs from Bitwarden": lambda w: w.env.update({"EXAMPLE_USER": CANARY + "-other"}),
    "a Secret without its key": lambda w: w.cluster["namespaces"]["example"]["secrets"]["example-files"]
    .update({"other": CANARY.encode()}) or w.cluster["namespaces"]["example"]["secrets"]["example-files"]
    .pop("token"),
    "nothing reachable": lambda w: w.http.clear(),
    "kubectl forbidden": lambda w: w.cluster.update({"forbidden": True}),
    "GitHub forbidden": lambda w: w.github.update({"forbidden": True}),
    "outside with-secrets": lambda w: w.env.pop("PUBKIT_SECRETS_PROJECT"),
}


@pytest.mark.parametrize("state", sorted(STATES))
@pytest.mark.parametrize("argv", [("deploy", "check"), ("deploy", "check", "--json"),
                                  ("deploy", "check", "--offline", "--json")])
def test_no_value_is_printed(world, capsys, state, argv):
    STATES[state](world)
    run(world, capsys, *argv)
    assert world.code in (0, 1)
    for text in world.outputs:
        assert CANARY not in text


def test_an_exception_holding_a_value_is_not_shown(world, capsys):
    class Leaky:
        type, grade, budget, offline, schema = "example.leaky", "none", 1.0, True, {}

        def default_name(self, params):
            return None

        def depends_on(self, need, needs):
            return []

        def check(self, need, ctx):
            raise ValueError(f"the value is {CANARY}")

        def fulfil(self, need, ctx):
            raise NotImplementedError("phase 2")

    need = Need("example.leaky", "one", {})
    journal = walk({need.id: need}, {"example.leaky": Leaky()}, world.context(), service={})
    world.journal = journal.document()
    (_, result), = journal.entries
    assert result.state == "unknown"
    assert result.summary == "the check failed (ValueError)"
    assert CANARY not in journal.json()


def test_a_value_in_the_rendered_manifests_is_not_shown(world, capsys):
    world.rendered += ("---\napiVersion: v1\nkind: Secret\nmetadata: {name: example-held, namespace: example}\n"
                       f"stringData: {{cookie: {CANARY}}}\n")
    run(world, capsys, "deploy", "check", "--offline")
    assert CANARY not in world.stdout + world.stderr


def test_every_result_of_the_sound_fixture_is_free_of_values(world):
    ctx = world.context()
    plan = load(world.root, world.config, ctx)
    journal = walk(plan.needs, types(), ctx, service={})
    world.journal = journal.document()
    assert all(isinstance(r, Result) for _, r in journal.entries)
    assert CANARY not in journal.json()
