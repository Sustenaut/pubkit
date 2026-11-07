"""Steps for pubkit deploy check, against fakes of everything it touches: the shared ones are deploy_steps.py's."""

from __future__ import annotations

from pytest_bdd import scenarios

from deploy_steps import *  # noqa: F403 - the shared steps, and the fixtures pytest-bdd keeps them as

scenarios("deploy-check.feature")
