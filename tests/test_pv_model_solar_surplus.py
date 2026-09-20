"""Tests for blocking forced discharge while PV is already exporting."""

import pandas as pd
import pytest

from custom_components.miser.pv_model import BatteryModel, InverterModel, PVsystemModel


def _model() -> PVsystemModel:
    model = PVsystemModel(
        inverter=InverterModel(inverter_efficiency=100, charger_efficiency=100),
        battery=BatteryModel(
            battery_capacity=10000,
            battery_minimum_soc=15,
            battery_current_limit=100,
            voltage=50,
        ),
    )
    model.initial_soc = 80
    model.solar = pd.Series(
        [1500.0, 500.0],
        index=pd.date_range("2026-08-16 12:00", periods=2, freq="30min", tz="UTC"),
        name="solar",
    )
    model.consumption = pd.Series(
        [1000.0, 1000.0],
        index=model.solar.index,
        name="consumption",
    )
    model.prices = pd.DataFrame(
        {"import": 30.0, "export": 15.0},
        index=model.solar.index,
    )
    return model


@pytest.mark.asyncio
async def test_forced_discharge_is_ignored_while_solar_is_surplus() -> None:
    model = _model()

    flows = await model.flows(slots=[(model.solar.index[0], -3000.0), (model.solar.index[1], -3000.0)])

    assert flows.loc[model.solar.index[0], "forced"] == 0
    assert flows.loc[model.solar.index[0], "battery"] == -500.0
    assert flows.loc[model.solar.index[0], "grid"] == 0.0
    assert flows.loc[model.solar.index[1], "forced"] == -3000.0


def test_whole_horizon_actions_do_not_offer_discharge_during_solar_surplus() -> None:
    model = _model()

    actions = model._whole_horizon_actions(
        energy=8000.0,
        energy_levels=[6500.0, 8000.0, 9000.0],
        requirement=-500.0,
        dt_hours=0.5,
    )

    assert all(forced_power is None or forced_power >= 0 for _energy, forced_power, _grid in actions)
