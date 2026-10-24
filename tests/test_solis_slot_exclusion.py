"""Programming one timed slot switches the opposite one off."""

from datetime import datetime, timezone

import pytest

from custom_components.miser.const import (
    CONTROL_BATTERY_VOLTAGE,
    CONTROL_INVERTER_MODE,
    CONTROL_TIMED_CHARGE_CURRENT,
    CONTROL_TIMED_CHARGE_END_HOURS,
    CONTROL_TIMED_CHARGE_ON,
    CONTROL_TIMED_CHARGE_SOC,
    CONTROL_TIMED_CHARGE_START_HOURS,
    CONTROL_TIMED_DISCHARGE_CURRENT,
    CONTROL_TIMED_DISCHARGE_END_HOURS,
    CONTROL_TIMED_DISCHARGE_ON,
    CONTROL_TIMED_DISCHARGE_SOC,
    CONTROL_TIMED_DISCHARGE_START_HOURS,
    DOMAIN,
)
from custom_components.miser.inverters.solis import SolisConnectInverter

ENTITIES = {
    CONTROL_BATTERY_VOLTAGE: "sensor.v",
    CONTROL_INVERTER_MODE: "select.mode",
    CONTROL_TIMED_CHARGE_ON: "switch.charge",
    CONTROL_TIMED_CHARGE_START_HOURS: "time.cs",
    CONTROL_TIMED_CHARGE_END_HOURS: "time.ce",
    CONTROL_TIMED_CHARGE_SOC: "number.csoc",
    CONTROL_TIMED_CHARGE_CURRENT: "number.ccur",
    CONTROL_TIMED_DISCHARGE_ON: "switch.discharge",
    CONTROL_TIMED_DISCHARGE_START_HOURS: "time.ds",
    CONTROL_TIMED_DISCHARGE_END_HOURS: "time.de",
    CONTROL_TIMED_DISCHARGE_SOC: "number.dsoc",
    CONTROL_TIMED_DISCHARGE_CURRENT: "number.dcur",
}
NOW = datetime(2026, 10, 24, 1, 0, tzinfo=timezone.utc)


class _State:
    def __init__(self, state, attributes=None) -> None:
        self.state = state
        self.attributes = attributes or {}


class _States:
    def __init__(self, states: dict) -> None:
        self.states = states

    def get(self, entity_id):
        return self.states.get(entity_id)


class _Services:
    def __init__(self, states: _States) -> None:
        self._states = states
        self.calls: list[tuple[str, str, dict]] = []

    async def async_call(self, domain, service, data, blocking=False):
        self.calls.append((domain, service, data))
        if domain == "switch":
            self._states.states[data["entity_id"]] = _State("on" if service == "turn_on" else "off")


class _Hass:
    def __init__(self, charge_on: str, discharge_on: str) -> None:
        self.states = _States(
            {
                "sensor.v": _State("54"),
                "select.mode": _State("Self-Use"),
                "switch.charge": _State(charge_on),
                "switch.discharge": _State(discharge_on),
                "number.ccur": _State("0"),
                "number.dcur": _State("0"),
            }
        )
        self.services = _Services(self.states)
        self.data = {DOMAIN: {"control_entities": dict(ENTITIES), "model_entities": {}, "config_entities": {}}}

    def switch(self, entity_id: str) -> str:
        return self.states.states[entity_id].state


@pytest.mark.asyncio
async def test_charge_turns_discharge_slot_off() -> None:
    hass = _Hass(charge_on="off", discharge_on="on")
    await SolisConnectInverter(hass).control_charge(NOW, NOW, 100, 3000)

    assert ("switch", "turn_off", {"entity_id": "switch.discharge"}) in hass.services.calls
    assert hass.switch("switch.charge") == "on"
    assert hass.switch("switch.discharge") == "off"


@pytest.mark.asyncio
async def test_discharge_turns_charge_slot_off() -> None:
    hass = _Hass(charge_on="on", discharge_on="off")
    await SolisConnectInverter(hass).control_discharge(NOW, NOW, 15, 3000)

    assert ("switch", "turn_off", {"entity_id": "switch.charge"}) in hass.services.calls
    assert hass.switch("switch.discharge") == "on"


@pytest.mark.asyncio
async def test_no_redundant_turn_off_when_opposite_already_off() -> None:
    hass = _Hass(charge_on="off", discharge_on="off")
    await SolisConnectInverter(hass).control_charge(NOW, NOW, 100, 3000)

    assert ("switch", "turn_off", {"entity_id": "switch.discharge"}) not in hass.services.calls


@pytest.mark.asyncio
async def test_confirmation_flags_opposite_slot_left_on() -> None:
    hass = _Hass(charge_on="on", discharge_on="on")
    mismatches = await SolisConnectInverter(hass)._control_mismatches("charging", NOW, NOW, 100, 3000)

    assert "the opposite timed slot is still enabled" in mismatches
