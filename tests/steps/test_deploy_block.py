"""Steps for the deploy block's schema, through pubkit sync --check and pubkit deploy: the shared ones are deploy_steps.py's."""

from __future__ import annotations

from pytest_bdd import scenarios

from deploy_steps import *  # noqa: F403 - the shared steps, and the fixtures pytest-bdd keeps them as

scenarios("deploy-block.feature")
