"""Discharge targets are kept above the inverter's over-discharge SOC.

A Solis rejects a timed-discharge target at or below its over-discharge SOC and
keeps the old target: on 23 Oct a 15% target left the slot at 54%.
"""

import pytest

from custom_components.miser.const import CONTROL_TIMED_DISCHARGE_SOC, MODEL_BATTERY_MINIMUM_SOC
from custom_components.miser.inverters.solis import SolisConnectInverter
from tests.test_solis_slot_exclusion import NOW, _Hass, _State


def _hass(overdischarge: str | None = "15") -> _Hass:
    hass = _Hass(charge_on="off", discharge_on="off")
    if overdischarge is not None:
        hass.data["miser"]["model_entities"][MODEL_BATTERY_MINIMUM_SOC] = "number.odsoc"
        hass.states.states["number.odsoc"] = _State(overdischarge)
    return hass


def _written_discharge_soc(hass: _Hass) -> float:
    entity_id = hass.data["miser"]["control_entities"][CONTROL_TIMED_DISCHARGE_SOC]
    values = [data["value"] for domain, service, data in hass.services.calls if data.get("entity_id") == entity_id]
    return values[-1]


@pytest.mark.asyncio
async def test_target_at_floor_is_raised_one_above() -> None:
    hass = _hass("15")
    await SolisConnectInverter(hass).control_discharge(NOW, NOW, 15, 3000)
    assert _written_discharge_soc(hass) == 16


@pytest.mark.asyncio
async def test_target_below_floor_is_raised() -> None:
    hass = _hass("15")
    await SolisConnectInverter(hass).control_discharge(NOW, NOW, 10, 3000)
    assert _written_discharge_soc(hass) == 16


@pytest.mark.asyncio
async def test_target_above_floor_is_unchanged() -> None:
    hass = _hass("15")
    await SolisConnectInverter(hass).control_discharge(NOW, NOW, 30, 3000)
    assert _written_discharge_soc(hass) == 30


@pytest.mark.asyncio
async def test_unknown_floor_leaves_target_unchanged() -> None:
    hass = _hass(None)
    await SolisConnectInverter(hass).control_discharge(NOW, NOW, 15, 3000)
    assert _written_discharge_soc(hass) == 15


@pytest.mark.asyncio
async def test_unavailable_floor_leaves_target_unchanged() -> None:
    hass = _hass("unavailable")
    await SolisConnectInverter(hass).control_discharge(NOW, NOW, 15, 3000)
    assert _written_discharge_soc(hass) == 15


@pytest.mark.asyncio
async def test_confirmation_expects_the_raised_target() -> None:
    hass = _hass("15")
    hass.states.states["number.dsoc"] = _State("16")
    hass.states.states["switch.discharge"] = _State("on")
    mismatches = await SolisConnectInverter(hass)._control_mismatches("discharging", NOW, NOW, 15, 3000)
    assert not any("target SOC" in m for m in mismatches)


@pytest.mark.asyncio
async def test_charge_target_is_not_floored() -> None:
    hass = _hass("15")
    inverter = SolisConnectInverter(hass)
    assert inverter._programmed_target_soc("charging", 10) == 10
