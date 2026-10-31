"""Setup must retry, not fail for good, when the inverter integration is missing."""

from types import SimpleNamespace

import pytest
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady

from custom_components.miser import _get_entities
from custom_components.miser.const import COST_FLOWS, COST_SLOTS, DOMAIN
from custom_components.miser.inverters.solis import SolisConnectInverter
from custom_components.miser.sensor import OptimiserSensor
from custom_components.miser.utils import infer_integration_device_name


def _hass():
    return SimpleNamespace(
        data={DOMAIN: {}},
        config_entries=SimpleNamespace(async_entries=lambda domain: []),
    )


def _entry(integration: str):
    return SimpleNamespace(data={"inverter_brand": "solis"}, options={"integration": integration})


@pytest.mark.asyncio
async def test_missing_inverter_integration_is_retried() -> None:
    # Regression: get_integration_entities returned None and the unpack raised
    # TypeError, which HA treats as a permanent setup failure.
    with pytest.raises(ConfigEntryNotReady):
        await _get_entities(_hass(), _entry("solisconnect"))


@pytest.mark.asyncio
async def test_unknown_inverter_integration_fails_with_a_clear_error() -> None:
    with pytest.raises(ConfigEntryError):
        await _get_entities(_hass(), _entry("not_an_inverter"))


def test_device_name_comes_from_model_entities_only() -> None:
    # Setup used to count control entities too and fall back to a guess, so it
    # could pick a different prefix from the one the config flow validated.
    defs = SolisConnectInverter.entity_defs
    ids = ["sensor.solis_battery_soc", "sensor.solis_today_energy_imported_from_grid", "sensor.other_battery_voltage"]
    assert infer_integration_device_name(ids, defs) == "solis"
    assert infer_integration_device_name(["sensor.other_battery_voltage"], defs) is None


def test_plan_attributes_are_not_recorded() -> None:
    assert {COST_FLOWS, COST_SLOTS} <= OptimiserSensor._unrecorded_attributes
