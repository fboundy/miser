"""SolisConnect work mode comes from the storage-control register, not the select.

The work-mode select can sit at "unknown". Trusting it made Miser re-select
Backup/Reserve every run, and each selection resets the inverter's Backup SOC
to 99%, wiping the reserve the user had just set.
"""

import pytest

from custom_components.miser.inverters.solis import SOLIS_CONNECT_STORAGE_CONTROL, SolisConnectInverter
from tests.test_solis_slot_exclusion import NOW, _Hass, _State


def _hass(register: str | None, select: str = "unknown") -> _Hass:
    hass = _Hass(charge_on="off", discharge_on="off")
    hass.states.states["select.mode"] = _State(select)
    if register is not None:
        hass.data["miser"]["control_entities"][SOLIS_CONNECT_STORAGE_CONTROL] = "sensor.storage_ctl"
        hass.states.states["sensor.storage_ctl"] = _State(register)
    return hass


def _select_calls(hass: _Hass) -> list:
    return [data["option"] for domain, service, data in hass.services.calls if domain == "select"]


@pytest.mark.asyncio
async def test_mode_read_from_register_when_select_unknown() -> None:
    assert await SolisConnectInverter(_hass("49")).get_mode() == "Backup/Reserve"
    assert await SolisConnectInverter(_hass("33")).get_mode() == "Self-Use"


@pytest.mark.asyncio
async def test_falls_back_to_select_without_register() -> None:
    assert await SolisConnectInverter(_hass(None, select="Self-Use")).get_mode() == "Self-Use"


@pytest.mark.asyncio
async def test_unrecognised_register_value_falls_back_to_select() -> None:
    assert await SolisConnectInverter(_hass("999", select="Self-Use")).get_mode() == "Self-Use"


@pytest.mark.asyncio
async def test_backup_mode_not_reselected_when_already_in_backup() -> None:
    """The bug: idle runs re-selected Backup/Reserve and reset the Backup SOC."""
    hass = _hass("49")
    inverter = SolisConnectInverter(hass)
    inverter.backup_mode = True

    assert await inverter.control_matches("idle", None, 0)
    await inverter.control_charge(NOW, NOW, 100, 3000)

    assert _select_calls(hass) == []


@pytest.mark.asyncio
async def test_switches_into_backup_once_from_self_use() -> None:
    hass = _hass("33")
    inverter = SolisConnectInverter(hass)
    inverter.backup_mode = True

    await inverter.control_charge(NOW, NOW, 100, 3000)

    assert _select_calls(hass) == ["Backup/Reserve"]
