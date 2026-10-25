"""Backup Mode: hold the inverter's Backup SOC and run it in backup/reserve mode."""

import asyncio

import pandas as pd
import pytest

from custom_components.miser.const import (
    CONF_BACKUP_MODE,
    CONF_BACKUP_SOC,
    CONTROL_BACKUP_MODE_SOC,
    CONTROL_STATE,
    COST_ENTITY_OBJECTS,
    DOMAIN,
    MODEL_BATTERY_MINIMUM_SOC,
)
from custom_components.miser.inverters.solis import SolisConnectInverter
from custom_components.miser.optimiser import _apply_inverter_control, _battery_floor_soc, _display_state
from custom_components.miser.pv_model import BatteryModel, InverterModel, PVsystemModel
from tests.test_optimiser_control_windows import _FakeModel, _RecordingController, _ValueEntity, _window_around_now
from tests.test_solis_slot_exclusion import NOW, _Hass, _State

# --- Which floor the plan uses ---


class _ConfigEntries:
    def async_entries(self, domain):
        return []


class _FloorHass:
    """Miser's Backup Mode switch and Backup SOC number, plus the inverter's 99% Backup SOC."""

    def __init__(self, backup: str, backup_soc: str | None, minimum: str = "15") -> None:
        states = {
            "switch.miser_backup_mode": _State(backup),
            "number.odsoc": _State(minimum),
            "number.inverter_backup_soc": _State("99"),  # must be ignored for planning
        }
        config = {CONF_BACKUP_MODE: "switch.miser_backup_mode"}
        if backup_soc is not None:
            states["number.miser_backup_soc"] = _State(backup_soc)
            config[CONF_BACKUP_SOC] = "number.miser_backup_soc"
        self.states = type("S", (), {"get": lambda _self, entity_id: states.get(entity_id)})()
        self.config_entries = _ConfigEntries()
        self.data = {
            DOMAIN: {
                "config_entities": config,
                "model_entities": {MODEL_BATTERY_MINIMUM_SOC: "number.odsoc"},
                "control_entities": {CONTROL_BACKUP_MODE_SOC: "number.inverter_backup_soc"},
            }
        }


def _model(min_soc: int = 15) -> PVsystemModel:
    return PVsystemModel(
        inverter=InverterModel(inverter_efficiency=97, charger_efficiency=91, charger_power=3000, inverter_power=3600),
        battery=BatteryModel(battery_capacity=20000, battery_minimum_soc=min_soc, battery_current_limit=60, voltage=50),
    )


def test_backup_off_uses_overdischarge_soc() -> None:
    assert asyncio.run(_battery_floor_soc(_FloorHass("off", "50"), _model())) == (False, 15.0)


def test_backup_on_uses_miser_backup_soc_not_inverters() -> None:
    """The inverter reads 99% after Backup/Reserve is selected; Miser's own value is used."""
    assert asyncio.run(_battery_floor_soc(_FloorHass("on", "60"), _model())) == (True, 60.0)


def test_backup_soc_below_overdischarge_is_raised_to_it() -> None:
    assert asyncio.run(_battery_floor_soc(_FloorHass("on", "10"), _model())) == (True, 15.0)


def test_unavailable_backup_soc_uses_default() -> None:
    assert asyncio.run(_battery_floor_soc(_FloorHass("on", "unavailable"), _model())) == (True, 50.0)


def test_missing_backup_soc_entity_uses_default() -> None:
    assert asyncio.run(_battery_floor_soc(_FloorHass("on", None), _model())) == (True, 50.0)


def test_plan_never_goes_below_backup_floor() -> None:
    """With the floor at 50% the plan still trades, but only above it."""
    model = _model(min_soc=50)
    idx = pd.date_range("2026-10-24 23:00", periods=14, freq="30min", tz="UTC")
    model.solar = pd.Series(0.0, index=idx, name="solar")
    model.consumption = pd.Series(800.0, index=idx, name="consumption")
    model.prices = pd.DataFrame({"import": 7.43, "export": 13.0}, index=idx)
    model.initial_soc = 70

    slots = asyncio.run(model.whole_horizon(soc_step_percent=5, state_resolution_wh=500))
    flows = asyncio.run(model.flows(slots=slots))

    assert flows["soc_end"].min() >= 50 - 0.01


# --- Inverter base mode ---


def _select_calls(hass: _Hass) -> list[str]:
    return [data["option"] for domain, service, data in hass.services.calls if domain == "select"]


@pytest.mark.asyncio
async def test_charge_uses_backup_reserve_when_backup_on() -> None:
    hass = _Hass(charge_on="off", discharge_on="off")
    inverter = SolisConnectInverter(hass)
    inverter.backup_mode = True
    await inverter.control_charge(NOW, NOW, 100, 3000)
    assert _select_calls(hass) == ["Backup/Reserve"]


@pytest.mark.asyncio
async def test_charge_uses_self_use_when_backup_off() -> None:
    hass = _Hass(charge_on="off", discharge_on="off")
    hass.states.states["select.mode"] = _State("Backup/Reserve")
    await SolisConnectInverter(hass).control_charge(NOW, NOW, 100, 3000)
    assert _select_calls(hass) == ["Self-Use"]


@pytest.mark.asyncio
async def test_idle_switches_to_backup_reserve_when_backup_on() -> None:
    hass = _Hass(charge_on="off", discharge_on="off")
    inverter = SolisConnectInverter(hass)
    inverter.backup_mode = True

    assert not await inverter.control_matches("idle", None, 0)  # still in Self-Use
    await inverter.control_idle()
    assert _select_calls(hass) == ["Backup/Reserve"]

    hass.states.states["select.mode"] = _State("Backup/Reserve")
    assert await inverter.control_matches("idle", None, 0)


@pytest.mark.asyncio
async def test_confirmation_expects_backup_reserve_when_backup_on() -> None:
    hass = _Hass(charge_on="on", discharge_on="off")
    inverter = SolisConnectInverter(hass)
    inverter.backup_mode = True
    mismatches = await inverter._control_mismatches("charging", NOW, NOW, 100, 3000)
    assert "mode is 'Self-Use', expected 'Backup/Reserve'" in mismatches


# --- Status ---


def test_display_state_shows_backup() -> None:
    assert _display_state("charging", backup=True) == "Charging (Backup)"
    assert _display_state("idle") == "Idle"


class _BackupAwareController(_RecordingController):
    def __init__(self) -> None:
        super().__init__()
        self.backup_mode = False
        self.backup_soc = None
        self.supports_backup_mode = True
        self.ensured: list[float | None] = []

    async def ensure_backup_soc(self) -> None:
        self.ensured.append(self.backup_soc)


@pytest.mark.asyncio
async def test_apply_sets_controller_mode_and_status() -> None:
    from tests.test_optimiser_control_windows import _FakeHass

    controller = _BackupAwareController()
    hass = _FakeHass(controller)
    state = _ValueEntity()
    hass.data[DOMAIN][COST_ENTITY_OBJECTS] = {CONTROL_STATE: state}
    model = _FakeModel(_window_around_now(3000.0, 3000.0, [55.0, 60.0, 65.0, 70.0, 75.0, 80.0]))
    model.backup_soc = 50.0

    await _apply_inverter_control(hass, model, schedule_checks=False)

    assert controller.backup_mode is True
    assert controller.backup_soc == 50.0
    assert controller.ensured == [50.0]  # reserve re-applied after control
    assert state.value == "Charging (Backup)"


# --- Keeping the inverter's Backup SOC at Miser's reserve ---


def _number_writes(hass: _Hass, entity_id: str) -> list:
    return [data["value"] for domain, service, data in hass.services.calls if domain == "number" and data.get("entity_id") == entity_id]


def _reserve_hass(inverter_value: str) -> _Hass:
    hass = _Hass(charge_on="off", discharge_on="off")
    hass.data[DOMAIN]["control_entities"][CONTROL_BACKUP_MODE_SOC] = "number.inverter_backup_soc"
    hass.states.states["number.inverter_backup_soc"] = _State(inverter_value)
    return hass


@pytest.mark.asyncio
async def test_reserve_rewritten_after_reset_to_99() -> None:
    hass = _reserve_hass("99")
    inverter = SolisConnectInverter(hass)
    inverter.backup_mode, inverter.backup_soc = True, 60
    await inverter.ensure_backup_soc()
    assert _number_writes(hass, "number.inverter_backup_soc") == [60]


@pytest.mark.asyncio
async def test_reserve_not_rewritten_when_already_set() -> None:
    hass = _reserve_hass("60")
    inverter = SolisConnectInverter(hass)
    inverter.backup_mode, inverter.backup_soc = True, 60
    await inverter.ensure_backup_soc()
    assert _number_writes(hass, "number.inverter_backup_soc") == []


@pytest.mark.asyncio
async def test_reserve_left_alone_when_backup_off() -> None:
    hass = _reserve_hass("99")
    inverter = SolisConnectInverter(hass)
    inverter.backup_mode, inverter.backup_soc = False, 60
    await inverter.ensure_backup_soc()
    assert _number_writes(hass, "number.inverter_backup_soc") == []
