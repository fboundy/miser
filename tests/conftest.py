"""Shared test fixtures for Miser."""

import pytest
from pytest_homeassistant_custom_component.common import MockModule, mock_integration


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations in Home Assistant tests."""


@pytest.fixture(autouse=True)
def mock_solcast_solar_dependency(hass):
    """Register the Solcast dependency required by the Miser manifest."""
    mock_integration(hass, MockModule("solcast_solar"))
