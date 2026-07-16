"""Shared fixtures for the daikin_madoka test suite."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make custom_components/ visible to the test hass instance."""
    yield
