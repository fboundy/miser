"""Tests that whole-horizon never commands power the battery cannot accept."""

import pandas as pd

from custom_components.miser.const import MODEL_MIN_SLOT_POWER
from custom_components.miser.pv_model import BatteryModel, InverterModel, PVsystemModel

CAPACITY = 10000
MINIMUM_SOC = 15
MIN_ENERGY = CAPACITY * MINIMUM_SOC / 100

# Lossless, so the expected energies below are plain arithmetic.
CHARGE_LIMIT = 3500.0
DISCHARGE_LIMIT = 3000.0
DT_HOURS = 0.5

ENERGY_LEVELS = [float(MIN_ENERGY), 3000.0, 5000.0, 7000.0, 9000.0, float(CAPACITY)]


def _model() -> PVsystemModel:
    model = PVsystemModel(
        inverter=InverterModel(inverter_efficiency=100, charger_efficiency=100),
        battery=BatteryModel(
            battery_capacity=CAPACITY,
            battery_minimum_soc=MINIMUM_SOC,
            battery_current_limit=100,
            voltage=50,
        ),
    )
    model.initial_soc = 100
    model.solar = pd.Series(
        [0.0, 0.0],
        index=pd.date_range("2026-09-03 12:00", periods=2, freq="30min", tz="UTC"),
        name="solar",
    )
    model.consumption = pd.Series([1000.0, 1000.0], index=model.solar.index, name="consumption")
    model.prices = pd.DataFrame({"import": 25.0, "export": 15.0}, index=model.solar.index)
    return model


def _actions(energy: float, requirement: float) -> list[tuple[float, float | None, float]]:
    return _model()._whole_horizon_actions(
        energy=energy,
        energy_levels=ENERGY_LEVELS,
        requirement=requirement,
        dt_hours=DT_HOURS,
    )


def _charges(actions) -> list[tuple[float, float, float]]:
    return [a for a in actions if a[1] is not None and a[1] >= MODEL_MIN_SLOT_POWER]


def _discharges(actions) -> list[tuple[float, float, float]]:
    return [a for a in actions if a[1] is not None and a[1] <= -MODEL_MIN_SLOT_POWER]


def test_full_battery_is_not_offered_forced_charging_during_solar_surplus() -> None:
    # The reported symptom: at 100% SOC the clamp made a full-power charge cost
    # exactly the same as idling, so the plan could emit it arbitrarily.
    assert _charges(_actions(energy=float(CAPACITY), requirement=-1500.0)) == []


def test_full_battery_is_not_offered_forced_charging_under_load() -> None:
    assert _charges(_actions(energy=float(CAPACITY), requirement=1200.0)) == []


def test_full_battery_can_still_discharge() -> None:
    powers = {power for _energy, power, _grid in _discharges(_actions(float(CAPACITY), 1200.0))}

    assert -DISCHARGE_LIMIT in powers


def test_empty_battery_is_not_offered_forced_discharging() -> None:
    assert _discharges(_actions(energy=MIN_ENERGY, requirement=1200.0)) == []


def test_empty_battery_can_still_charge() -> None:
    powers = {power for _energy, power, _grid in _charges(_actions(MIN_ENERGY, 1200.0))}

    assert CHARGE_LIMIT in powers


def test_mid_soc_still_offers_the_full_power_limits() -> None:
    actions = _actions(energy=5000.0, requirement=1200.0)
    powers = {power for _energy, power, _grid in actions if power is not None}

    assert CHARGE_LIMIT in powers
    assert -DISCHARGE_LIMIT in powers


def test_partial_headroom_clips_charge_to_what_fits() -> None:
    # 1000 Wh of headroom over half an hour is 2000 W, well under the 3500 W limit.
    actions = _charges(_actions(energy=9000.0, requirement=1200.0))
    limit_actions = [a for a in actions if a[1] > 2000.0]

    assert limit_actions == []
    assert any(power == 2000.0 and energy == float(CAPACITY) for energy, power, _grid in actions)


def test_partial_headroom_clips_discharge_to_what_remains() -> None:
    # 500 Wh above the floor over half an hour is 1000 W, well under the 3000 W limit.
    actions = _discharges(_actions(energy=2000.0, requirement=1200.0))
    limit_actions = [a for a in actions if a[1] < -1000.0]

    assert limit_actions == []
    assert any(power == -1000.0 and energy == MIN_ENERGY for energy, power, _grid in actions)


def test_idle_action_is_always_offered() -> None:
    for energy in (MIN_ENERGY, 5000.0, float(CAPACITY)):
        assert any(power is None for _energy, power, _grid in _actions(energy, 1200.0))
