"""Programming one timed slot switches the opposite one off."""

from datetime import datetime, timezone

import pytest
from homeassistant.util import dt as dt_util

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
from custom_components.miser.inverters.solis import SOLIS_CONNECT_TOU_SWITCH, SolisConnectInverter

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


def _with_registers(hass: _Hass, switch: str, charge_start: tuple[str, str]) -> _Hass:
    hass.data[DOMAIN]["control_entities"].update(
        {
            SOLIS_CONNECT_TOU_SWITCH: "sensor.tou",
            "tou_charge_start_hour": "sensor.csh",
            "tou_charge_start_minute": "sensor.csm",
        }
    )
    hass.states.states.update(
        {"sensor.tou": _State(switch), "sensor.csh": _State(charge_start[0]), "sensor.csm": _State(charge_start[1])}
    )
    return hass


@pytest.mark.asyncio
async def test_confirmation_reads_inverter_registers_not_optimistic_switches() -> None:
    # Overnight 2026-10-31: SolisConnect showed the charge slot on while the inverter held it off.
    local = dt_util.as_local(NOW)  # the HA test harness runs in US/Pacific
    hass = _with_registers(_Hass(charge_on="on", discharge_on="off"), switch="64", charge_start=("0", "30"))
    # The optimistic entity agrees with the request; only the register shows 00:30.
    hass.states.states["time.cs"] = _State(local.strftime("%H:%M:00"))
    mismatches = await SolisConnectInverter(hass)._control_mismatches("charging", NOW, NOW, 100, 3000)

    assert "charging enable switch is off" in mismatches
    assert "the opposite timed slot is still enabled" in mismatches
    assert f"start is 00:30, expected {local:%H:%M}" in mismatches


@pytest.mark.asyncio
async def test_confirmation_passes_when_registers_match() -> None:
    local = dt_util.as_local(NOW)
    hass = _with_registers(_Hass(charge_on="on", discharge_on="off"), switch="1", charge_start=(str(local.hour), str(local.minute)))
    mismatches = await SolisConnectInverter(hass)._control_mismatches("charging", NOW, NOW, 100, 3000)

    assert "charging enable switch is off" not in mismatches
    assert "the opposite timed slot is still enabled" not in mismatches
    assert not any(m.startswith("start is") for m in mismatches)


@pytest.mark.asyncio
async def test_slot_times_written_after_soc_and_current_then_register_saved() -> None:
    # Overnight 2026-11-02: an SOC/current write after the times put them back to the saved
    # window, and with the slot already on nothing wrote 43707 to make new times take effect.
    hass = _with_registers(_Hass(charge_on="on", discharge_on="off"), switch=str(1 | 64 | 2), charge_start=("6", "13"))
    await SolisConnectInverter(hass).control_charge(NOW, NOW, 100, 3000)

    order = [data.get("entity_id", service) for _domain, service, data in hass.services.calls]
    assert order.index("number.csoc") < order.index("time.cs")
    assert order.index("number.ccur") < order.index("time.cs")
    domain, service, data = hass.services.calls[-1]
    # Charge bit set, discharge bit cleared, slot-2 bit (2) untouched.
    assert (domain, service, data) == ("solisconnect", "solis_write_holding_register", {"address": 43707, "value": 1 | 2})


@pytest.mark.asyncio
async def test_current_capped_at_the_datasheet_limit_at_low_voltage() -> None:
    # RHI-3.6K-48ES-5G: 3 kW / 62.5 A; on 2026-11-02 a 70 A write reverted within 15 s.
    from custom_components.miser.pv_model import InverterModel

    hass = _Hass(charge_on="off", discharge_on="off")
    hass.data[DOMAIN]["inverter_model"] = InverterModel(97, 91, 3600, 3000)
    controller = SolisConnectInverter(hass)

    hass.states.states["sensor.v"] = _State("44")
    assert await controller.power_to_current(3000) == pytest.approx(62.5)
    hass.states.states["sensor.v"] = _State("51.5")
    assert await controller.power_to_current(3000) == pytest.approx(3000 / 51.5)
