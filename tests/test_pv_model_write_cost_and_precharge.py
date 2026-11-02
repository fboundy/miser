"""Whole Horizon prices every control change and keeps one power across a same-price run;
the heuristic discharge pass fills from earlier cheap slots for a valuable export.

Regression (2026-11-02): Whole Horizon planned 3297/2857/440/2198 W across a flat-price night,
and without Whole Horizon nothing charged before a 100p Axle export, so it only exported what
was already in the battery.
"""

import pandas as pd
import pytest

from custom_components.miser.pv_model import BatteryModel, InverterModel, PVsystemModel


def _model(idx, imp, exp, initial_soc, consumption=500.0) -> PVsystemModel:
    model = PVsystemModel(
        inverter=InverterModel(97, 91, 3600, 3000),
        battery=BatteryModel(battery_capacity=20000, battery_minimum_soc=15, battery_current_limit=60),
    )
    model.consumption = pd.Series(consumption, index=idx, name="consumption")
    model.solar = pd.Series(0.0, index=idx, name="solar")
    model.prices = pd.DataFrame({"import": imp, "export": exp}, index=idx)
    model.initial_soc = initial_soc
    return model


@pytest.mark.asyncio
async def test_whole_horizon_charges_a_flat_price_night_at_one_power() -> None:
    idx = pd.date_range("2026-11-03 00:00", periods=14, freq="30min", tz="UTC")
    # Flat night, then a dear day the battery should be filled for.
    imp = pd.Series(7.43, index=idx)
    imp[idx.hour >= 5] = 30.0
    model = _model(idx, imp, 0.0, initial_soc=15, consumption=1500.0)
    model.whole_horizon_write_cost = 5
    model.valuation_prices = None

    plan = await model.whole_horizon()
    night = [power for start, power in plan if start.hour < 5]

    assert night, "expected the cheap night to be used"
    assert len(set(night)) == 1


@pytest.mark.asyncio
async def test_averaging_keeps_runs_at_different_prices_apart() -> None:
    idx = pd.date_range("2026-11-03 00:00", periods=4, freq="30min", tz="UTC")
    model = _model(idx, pd.Series([5.0, 5.0, 6.0, 6.0], index=idx), 0.0, initial_soc=15)
    flows = await model.flows(slots=[])

    path = [(idx[0], 3000.0), (idx[1], 1000.0), (idx[2], 2000.0), (idx[3], 400.0)]
    averaged = await model._average_same_price_runs(flows, path)

    assert [p for _s, p in averaged] == [2000.0, 2000.0, 1200.0, 1200.0]


def test_power_change_counts_as_a_write() -> None:
    model = _model(pd.date_range("2026-11-03", periods=1, freq="30min", tz="UTC"), 5.0, 0.0, 15)

    assert model._whole_horizon_control(3000.0) != model._whole_horizon_control(2000.0)
    assert model._whole_horizon_control(None) == model._whole_horizon_control(5.0) == "idle"


@pytest.mark.asyncio
async def test_discharge_pass_precharges_for_a_valuable_export() -> None:
    idx = pd.date_range("2026-11-02 14:00", "2026-11-02 20:00", freq="30min", tz="UTC", inclusive="left")
    imp = pd.Series(30.0, index=idx)
    exp = pd.Series(13.0, index=idx)
    exp[idx.hour == 17] = 100.0  # an Axle-style export event
    model = _model(idx, imp, exp, initial_soc=20)
    model.base_cost = model.best_cost = (await model.net_cost(slots=[])).sum()

    slots = await model.discharging(base_slots=[])
    flows = await model.flows(slots=slots)

    assert (flows.loc[idx.hour < 17, "forced"] > 0).any(), "expected a charge before the event"
    assert (flows.loc[idx.hour == 17, "forced"] <= -model.discharge_power_limit + 1).all()
