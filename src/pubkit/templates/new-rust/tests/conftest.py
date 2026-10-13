"""Fixtures for {name}'s functional suite. The binary is resolved by
pubkit's `binary` fixture, from {bin_var}."""

import pytest


@pytest.fixture
def program(binary):
    return binary("{name}")
